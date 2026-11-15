#!/usr/bin/env python3
"""Check frozen engine bytes and enumerate every engineering delta without imports.

The stage-0 manifest is the authority. Engineering approvals are pinned to exact
old/new hashes, so a later edit becomes pending review again. This check neither
loads a model nor executes the baseline or adapted scientific engine.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PREFIX = "Analysis Package/project_leap_2d/"
FROZEN_CATEGORIES = frozenset({
    "scientific_core",
    "scientific_configuration_or_data_contract",
    "pinned_model_resource",
})

# Filled only after inspecting the complete old/new delta. Keyed by relative path;
# new modules use baseline_sha256=None. An approval cannot waive frozen categories.
APPROVED_ENGINEERING_DIFFS: dict[str, dict[str, Any]] = {'analysis_controller.py': {'baseline_sha256': '1b6d8f6e23080924361a316d2cfc3516c662203eacee5bea516c95fdbb05c90a',
                            'current_sha256': '4d09f1e3beedd70f6c26f35d1931e3a1378c8d383798edfad42ef024b04ee805',
                            'reason': 'Emit progress only inside existing successful completion callbacks: '
                                      'one per ROI candidate and deduplicated Cellpose Z window. The added '
                                      'set tracks reported window positions only; no candidate, scheduling, '
                                      'ROI, parameter, model or selection value is changed.'},
 'desktop_adapter.py': {'baseline_sha256': None,
                        'current_sha256': 'd0189d125b60d5aa79df9122425806cd8095b3b5226ea5d356e4566feda09edf',
                        'reason': 'Validate isolated requests and locks; persist this worker PID/request '
                                  'identity, hold an exclusive application analysis lock plus the shared '
                                  'component lock, then call the unchanged scientific workflow. Capture '
                                  'actual code/model provenance and publish the validated six-output '
                                  'transaction. Duplicate analysis is rejected with an explicit error; '
                                  'successful commit retains artifacts after later cleanup failure.'},
 'desktop_events.py': {'baseline_sha256': None,
                       'current_sha256': 'b23e5c1025e3937e3c5a5e567fe773264e3fb0bc17cb56409d6ac6188919549c',
                       'reason': 'New JSONL events and owned-process bookkeeping. Signal handling delays '
                                 'cancellation across publication; no scientific data arrays, model state or '
                                 'settings are modified.'},
 'desktop_paths.py': {'baseline_sha256': None,
                      'current_sha256': 'b86c93a4fdc2ac7172a27244b8865d1dd0e911418ffe9614927f1ac0884701fd',
                      'reason': 'Add a worker-owned exclusive analysis lock to prevent simultaneous '
                                'scientific workers after GUI failure; retain component shared locking and '
                                'validated isolated paths/caches. Both Fiji paths receive the verified '
                                'task-local Java preferences environment. No device, inference, thread, heap '
                                'or scientific parameters are modified.'},
 'fiji_review/cell_edit_fiji_bridge.py': {'baseline_sha256': 'f028e975c55eac66c689eb02a350b7326265526888d4a44752e37012f34238bf',
                                          'current_sha256': 'fce4f6cca719dac982b6f0945a552d6aa67af1000452eb2c9a92c146e1daecc9',
                                          'reason': 'Inject isolated cache root and the shared task-local '
                                                    'Java environment; register the owned Fiji session, poll '
                                                    'cancellation and emit review events. Existing review, '
                                                    'cell editing, launch heap, ROI and measurement '
                                                    'operations remain unchanged.'},
 'fiji_review/cell_edit_worker.py': {'baseline_sha256': '778c1c4b6f4702ad6314eb7548ba28a740fd9ee397e748c3f92c70da5f36a6f5',
                                     'current_sha256': '42a9f1c23ce0fa23573ec743b2e5dec631c3f48dd1ef2a844826ad62fea7022b',
                                     'reason': 'Register the existing start_new_session worker and check '
                                               'task cancellation before launch and during its wait loop. '
                                               'Scientific action implementation and worker arguments remain '
                                               'unchanged.'},
 'fiji_review/failed_run_retention.py': {'baseline_sha256': '22380060b1e82aac9ff86bc5cf3d3db7c786e2eb62a02d99e9de1a0583c6ee15',
                                         'current_sha256': '49f60a818f381a2ac191cc683879c02710ffec52f617421656e8deb3ab20af7a',
                                         'reason': 'Replace only the production cache-root expression with '
                                                   'the validated desktop cache accessor; retain failed-run '
                                                   'ownership and cleanup checks.'},
 'fiji_review/fiji_launcher.py': {'baseline_sha256': 'c0d496f604d185cac5aa295a366990c0dd46716da2498e9f22a0bfba1b3d5925',
                                  'current_sha256': '19163d6301127d3ac1ea39306d97d4556f97e64d230a4aade467e56253b3805d',
                                  'reason': 'Require the explicit managed launcher in desktop mode and '
                                            'task-local Java preferences environment; track and clean up '
                                            'only the spawned session. Existing Fiji 16G heap and scientific '
                                            'launch arguments are retained.'},
 'fiji_review/resources/astrocyte_roi_reviewer.groovy': {'baseline_sha256': '4bb3d078a020729b1c91f647fcf0bceb12c3b8595d10f6ef37c014a18489df97',
                                                         'current_sha256': '2b48e95571db7385277c0d80019343cae1e0d516fe2a270605c08694e90f925a',
                                                         'reason': 'Record source calibration and serialize '
                                                                   'cloned final ROI geometry/identity after '
                                                                   'existing validation, numbering and raw '
                                                                   'measurement. All pre-existing ROI edits, '
                                                                   'projection and measurement statements '
                                                                   'are retained.'},
 'fiji_review/resources/desktop-preferences.jar': {'baseline_sha256': None,
                                                   'current_sha256': '4312e684668576ca7f9d92ef30a8850f549c297d79a59c5c31757e0ff6c5d7f6',
                                                   'reason': 'Binary was independently rebuilt from the '
                                                             'reviewed TaskPreferencesFactory.java with '
                                                             'tools/build_desktop_preferences.py --check; '
                                                             'exact jar bytes match. Contains only the '
                                                             'reviewed factory and Store class, compiled for '
                                                             'Java 8 with fixed archive metadata.'},
 'fiji_review/resources/java/org/projectleap/desktop/TaskPreferencesFactory.java': {'baseline_sha256': None,
                                                                                    'current_sha256': 'a403d0ac4affcc05e20cef5ff95621f0cc47534b7dab816513180abf98cf10c7',
                                                                                    'reason': 'Small '
                                                                                              'PreferencesFactory '
                                                                                              'stores Java '
                                                                                              'user/system '
                                                                                              'preferences '
                                                                                              'in required '
                                                                                              'per-task '
                                                                                              'property '
                                                                                              'paths, '
                                                                                              'avoiding '
                                                                                              'macOS '
                                                                                              'CFPreferences '
                                                                                              'writes '
                                                                                              'outside the '
                                                                                              'task. Source '
                                                                                              'contains only '
                                                                                              'preference '
                                                                                              'serialization, '
                                                                                              'no image, '
                                                                                              'model or '
                                                                                              'measurement '
                                                                                              'logic. '
                                                                                              'Cross-JVM '
                                                                                              'read/flush/remove '
                                                                                              'probes pass.'},
 'fiji_review/review_protocol.py': {'baseline_sha256': 'd56f09cc10747252f8b915565a3761c1a466e7a0cc7570f9d015d993f87e1658',
                                    'current_sha256': '49753f169afcae2b68decb8ab5d2e348b475d4482e715d1cae8a767f949c916d',
                                    'reason': 'Inject the isolated cache root and restrict TTL cleanup to '
                                              'non-symlink UUID run directories with a manifest. Initial '
                                              'geometry, projection, calibration and Fiji scientific '
                                              'settings are unchanged.'},
 'final_roi_archive.py': {'baseline_sha256': None,
                          'current_sha256': 'fd9727977a5d0eb3e1da93b1bc4b1329512e498691f70914fd3e78f4aa521736',
                          'reason': 'Validate and atomically archive only Fiji-final geometry/identities and '
                                    'required runtime provenance. Pixel bounds accept int or finite exactly '
                                    'integral float because actual Groovy Rectangle getters serialize values '
                                    'such as 991.0; fractional/nonfinite/bool values remain rejected. This '
                                    'changes serialization validation, never ROI geometry or measurement '
                                    'values. Real retained-run source hashes remained unchanged.'},
 'run_state.py': {'baseline_sha256': '56b4cc3883971bce1ad40087d7465233b72746f72aba9a797a39f938e08fe635',
                  'current_sha256': 'dfba229b76c465ba36702e029bcdba6b39e108ad330a13f6b5325581d6cd6fd4',
                  'reason': 'Add explicit stage-event mapping and task cancellation at existing stage '
                            'boundaries. The original timing and scientific execution statements are '
                            'unchanged.'},
 'runtime_manifest.py': {'baseline_sha256': '4eebb2cdd22565e3fe5fc973651e085368802b2a1d5f53c074dc809475445252',
                         'current_sha256': '90bbe09a6d6cdedf1b51542790c0c03a17856b74614142c3db77680b388a677f',
                         'reason': 'Update only GROOVY_RESOURCE_SHA256 to the reviewed reviewer-resource '
                                   'bytes. Module order, required symbols, model identity and historical '
                                   'source identifier are unchanged.'},
 'workspace/folder_checks.py': {'baseline_sha256': 'db515daae120316e0fbc09397ab6fcaaf1386d5b79dd63bd64b5c8d35f4a9664',
                                'current_sha256': 'aa7eedcd982225e32d31068927d14b4388926a91da9bdbce1320cf33fba17337',
                                'reason': 'Resolve the analysis lock under the isolated cache root. No '
                                          'input, scientific quality or morphology checks are changed.'},
 'workspace/publication_recovery.py': {'baseline_sha256': '271fedaff1f6331342a3ba5808e253c7df86cde7e616b55d535002ef8ddd4df8',
                                       'current_sha256': 'd48dfc4cd864bf11e9fc72f0ba04f7188793d136378aa824cbf20757cf2cf99c',
                                       'reason': 'Extend the formal atomic bundle from five to six outputs '
                                                 'with roi_archive; update schema 2 to 3, key validation, '
                                                 'temporary-file pattern and corresponding messages '
                                                 'together. Original transaction and rollback operations are '
                                                 'retained.'}}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _syntax_inventory(text: str) -> dict[str, dict[str, Any]]:
    """Top-level AST records retain function bodies and all scientific constants."""
    tree = ast.parse(text)
    inventory: dict[str, dict[str, Any]] = {}
    occurrences: Counter[str] = Counter()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            label = f"{type(node).__name__}:{node.name}"
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            label = "assignment:" + ",".join(ast.dump(t, include_attributes=False) for t in targets)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            label = "import:" + ast.dump(node, include_attributes=False)
        else:
            label = type(node).__name__
        occurrences[label] += 1
        key = f"{label}#{occurrences[label]}"
        dumped = ast.dump(node, include_attributes=False)
        inventory[key] = {
            "sha256": hashlib.sha256(dumped.encode()).hexdigest(),
            "start_line": node.lineno,
            "end_line": node.end_lineno,
        }
    return inventory


def describe_delta(old_text: str, new_text: str, relative_path: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "unified_diff": "".join(difflib.unified_diff(
            old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
            fromfile=f"stage0/{relative_path}", tofile=f"engine/{relative_path}", n=3,
        )),
    }
    if relative_path.endswith(".py"):
        try:
            old_nodes = _syntax_inventory(old_text)
            new_nodes = _syntax_inventory(new_text)
        except SyntaxError as exc:
            result["syntax_error"] = str(exc)
        else:
            result["changed_python_nodes"] = [
                {"node": key, "baseline": old_nodes.get(key), "current": new_nodes.get(key)}
                for key in sorted(set(old_nodes) | set(new_nodes))
                if old_nodes.get(key, {}).get("sha256") != new_nodes.get(key, {}).get("sha256")
            ]
            result["unchanged_python_nodes"] = sum(
                old_nodes[key]["sha256"] == new_nodes.get(key, {}).get("sha256")
                for key in old_nodes
            )
    return result


def inspect_engine(
    baseline_path: Path,
    engine_root: Path,
    approvals: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    baseline = json.loads(baseline_path.read_text())
    approvals = APPROVED_ENGINEERING_DIFFS if approvals is None else approvals
    source_root = Path(baseline["source_root"])
    frozen = []
    changed = []
    source_issues = []
    source_records = []
    known_paths = set()
    for record in baseline["file_manifest"]:
        baseline_relative = record["relative_path"]
        if not baseline_relative.startswith(BASELINE_PREFIX):
            continue
        relative = baseline_relative[len(BASELINE_PREFIX):]
        known_paths.add(relative)
        original = source_root / baseline_relative
        target = engine_root / relative
        original_hash = sha256(original) if original.is_file() and not original.is_symlink() else None
        current_hash = sha256(target) if target.is_file() and not target.is_symlink() else None
        original_matches = original_hash == record["sha256"]
        source_records.append({"path": relative, "sha256": original_hash, "matches_stage0": original_matches})
        if not original_matches:
            source_issues.append({"path": str(original), "expected_sha256": record["sha256"], "actual_sha256": original_hash})
        item = {
            "path": relative,
            "category": record["category"],
            "baseline_sha256": record["sha256"],
            "current_sha256": current_hash,
            "unchanged": current_hash == record["sha256"],
        }
        if record["category"] in FROZEN_CATEGORIES:
            frozen.append(item)
        elif not item["unchanged"]:
            if original_matches and target.is_file() and not target.is_symlink():
                try:
                    item.update(describe_delta(original.read_text(), target.read_text(), relative))
                except UnicodeError:
                    item["binary_delta"] = True
            else:
                item["diff_unavailable"] = "Baseline bytes changed, or adapted file is missing or a symlink."
            changed.append(item)

    for target in sorted(engine_root.rglob("*")):
        if not target.is_file() or "__pycache__" in target.parts or target.name == ".DS_Store":
            continue
        relative = target.relative_to(engine_root).as_posix()
        if relative in known_paths:
            continue
        item = {
            "path": relative, "category": "new_engineering_module_or_resource",
            "baseline_sha256": None, "current_sha256": sha256(target), "unchanged": False,
        }
        if target.is_symlink():
            item["diff_unavailable"] = "New engine symlink is not an approved source file."
        else:
            try:
                item.update(describe_delta("", target.read_text(), relative))
            except UnicodeError:
                item["binary_delta"] = True
        changed.append(item)

    for item in changed:
        approval = approvals.get(item["path"], {})
        approved = (
            bool(approval.get("reason"))
            and approval.get("baseline_sha256") == item["baseline_sha256"]
            and approval.get("current_sha256") == item["current_sha256"]
            and not item.get("syntax_error") and not item.get("diff_unavailable")
        )
        item["review_status"] = "approved_engineering_only" if approved else "pending_review"
        if approved:
            item["review_reason"] = approval["reason"]

    frozen_ok = bool(frozen) and all(item["unchanged"] for item in frozen)
    review_ok = all(item["review_status"] == "approved_engineering_only" for item in changed)
    return {
        "schema_version": 1,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "baseline_path": str(baseline_path.resolve()),
        "baseline_manifest_sha256": sha256(baseline_path),
        "engine_root": str(engine_root.resolve()),
        "original_engine_matches_stage0": not source_issues,
        "scientific_core_unchanged": frozen_ok,
        "engineering_review_complete": review_ok,
        "ok": frozen_ok and review_ok and not source_issues,
        "frozen_file_count": len(frozen),
        "frozen_category_counts": dict(Counter(item["category"] for item in frozen)),
        "frozen_files": frozen,
        "engineering_changes": changed,
        "original_source_issues": source_issues,
        "original_source_files": source_records,
        "limits": [
            "Static byte and reviewed source-diff evidence; no model, Fiji or image analysis executed.",
            "Byte agreement does not establish biological validity or performance parity.",
            "GUI, importer and maintenance behavior are reviewed separately in REVIEW.md and targeted tests.",
        ],
    }


def main() -> int:
    """Check the maintained source against the fixed scientific baseline."""
    from build_app import source_inventory, scientific_source_evidence
    from verify_app_bundle import scientific_source_contract
    parser = argparse.ArgumentParser(description="Verify the frozen fluorescence scientific source.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    evidence = scientific_source_evidence()
    report = scientific_source_contract(source_inventory(), evidence["scientific_baseline_source"],
                                        evidence["engineering_adapter_changes"],
                                        evidence["engineering_pruning"])
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
