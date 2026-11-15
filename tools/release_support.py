"""Public release settings and Keychain-backed signing for publisher tools.

This module is not shipped inside the desktop application. Signature verification
uses the same CryptoKit implementation as the application and needs no private key.
"""
from __future__ import annotations

import base64
import binascii
import ctypes
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SETTINGS = ROOT / 'tools/build/release-settings.json'
COMPONENT_IDS = frozenset(('project-leap-2d', 'cell-counting', 'science-runtime', 'cellpose-model', 'fiji'))
VERSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')


def require_https(value):
    if not isinstance(value, str):
        raise ValueError('Release URL must be text.')
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Release URLs must use HTTPS without credentials or fragments.')
    return value


def decode_base64(value, length, label):
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError, binascii.Error) as error:
        raise ValueError(label + ' is not valid Base64.') from error
    if len(raw) != length:
        raise ValueError(label + ' has the wrong length.')
    return raw


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def load_settings(path=None):
    settings = json.loads(Path(path or DEFAULT_SETTINGS).read_text(encoding='utf-8'), object_pairs_hook=_unique_object)
    if not isinstance(settings, dict) or settings.get('schema_version') != 1:
        raise ValueError('Unsupported release-settings schema.')
    application = settings.get('application')
    components = settings.get('components')
    if not isinstance(application, dict) or not isinstance(components, dict):
        raise ValueError('Application and component settings are required.')
    for value in (settings.get('website_url'), application.get('feed_url'), application.get('archive_url'), components.get('catalog_url')):
        require_https(value)
    for section in (application, components):
        decode_base64(section.get('public_key'), 32, 'Release public key')
        account = section.get('keychain_account')
        if not isinstance(account, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', account):
            raise ValueError('An explicit Keychain account is required.')
    if application['public_key'] == components['public_key'] or application['keychain_account'] == components['keychain_account']:
        raise ValueError('Application updates and component catalogs must use separate signing identities.')
    build = application.get('first_public_build')
    if type(build) is not int or build != 1:
        raise ValueError('The public build sequence starts at 1.')
    if not isinstance(application.get('version'), str) or not VERSION.fullmatch(application['version']):
        raise ValueError('Application version is invalid.')
    if not isinstance(application.get('name'), str) or not application['name'].strip():
        raise ValueError('Application name is required.')
    if application.get('bundle_identifier') != 'org.projectleap.prototype.d':
        raise ValueError('The application bundle identity must be preserved.')
    if not isinstance(application.get('minimum_macos'), str) or not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', application['minimum_macos']):
        raise ValueError('Minimum macOS version is invalid.')
    filename = application.get('archive_filename')
    if not isinstance(filename, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*\.dmg', filename):
        raise ValueError('The application archive must have an ASCII DMG filename.')
    if urlsplit(application['archive_url']).path.rsplit('/', 1)[-1] != filename:
        raise ValueError('Application archive filename and URL disagree.')
    assets = components.get('assets')
    if not isinstance(assets, dict) or set(assets) != COMPONENT_IDS:
        raise ValueError('The five component release targets are required.')
    addresses = []
    for identity, item in assets.items():
        if not isinstance(item, dict) or not isinstance(item.get('version'), str) or not VERSION.fullmatch(item['version']):
            raise ValueError('Invalid component release target: ' + identity)
        addresses.append(require_https(item.get('url')))
    if len(set(addresses)) != len(addresses):
        raise ValueError('Each component archive needs a distinct download URL.')
    repositories = settings.get('module_repositories')
    if not isinstance(repositories, dict) or set(repositories) != {'fluorescence', 'counting'}:
        raise ValueError('Both module repository addresses are required.')
    for value in repositories.values():
        require_https(value)
    return settings


def component_configuration(settings):
    value = settings['components']
    return {'schema_version': 1, 'catalog_url': value['catalog_url'], 'public_key': value['public_key']}


def sparkle_configuration(settings):
    value = settings['application']
    return {'SUFeedURL': value['feed_url'], 'SUPublicEDKey': value['public_key'],
            'SUEnableAutomaticChecks': False, 'SUAutomaticallyUpdate': False,
            'SUVerifyUpdateBeforeExtraction': True}


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True, allow_nan=False).encode('utf-8')


def sha256_file(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


class PublicVerifier:
    """One temporary native verifier shared across a publisher invocation."""
    def __enter__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='leap-release-verify-')
        folder = Path(self.temporary.name)
        library = folder / 'libReleaseVerification.dylib'
        try:
            subprocess.run(['xcrun', 'swiftc', '-O', '-emit-library', '-module-cache-path', str(folder / 'module-cache'),
                            str(ROOT / 'native/catalog_signature.swift'), '-o', str(library)], check=True, capture_output=True)
            subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(library)], check=True, capture_output=True)
            self.library = ctypes.CDLL(str(library))
            self.function = self.library.leap_verify_ed25519
            self.function.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
            self.function.restype = ctypes.c_int32
        except (subprocess.CalledProcessError, OSError, AttributeError):
            self.temporary.cleanup()
            raise
        return self

    def verify(self, payload, signature, public_key):
        key = decode_base64(public_key, 32, 'Public key')
        signed = decode_base64(signature, 64, 'Signature')
        if not isinstance(payload, bytes):
            raise TypeError('Signature verification requires the exact payload bytes.')
        buffers = [ctypes.create_string_buffer(value) for value in (key, signed, payload)]
        if self.function(buffers[0], len(key), buffers[1], len(signed), buffers[2], len(payload)) != 1:
            raise ValueError('Release signature verification failed.')

    def __exit__(self, *_):
        self.temporary.cleanup()


def sign_bytes(payload, public_key, account, sparkle_bin, verifier):
    """Sign exact bytes through the named Keychain item; never export a key."""
    if not isinstance(payload, bytes):
        raise TypeError('Signing requires exact payload bytes.')
    tool = Path(sparkle_bin) / 'sign_update'
    if not tool.is_file():
        raise ValueError('Sparkle sign_update was not found in the supplied bin directory.')
    with tempfile.TemporaryDirectory(prefix='leap-release-sign-') as temporary:
        path = Path(temporary) / 'payload.json'
        path.write_bytes(payload)
        signature = subprocess.check_output([str(tool), '--account', account, '-p', str(path)], text=True).strip()
        if path.read_bytes() != payload:
            raise ValueError('The signing tool changed the payload.')
    verifier.verify(payload, signature, public_key)
    return signature
