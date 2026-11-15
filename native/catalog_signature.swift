import Foundation
import CryptoKit

// Verify exact publisher-signed bytes. The public key is supplied exclusively
// from the application's build configuration, never the downloaded envelope.
@_cdecl("leap_verify_ed25519")
public func leapVerifyEd25519(
    _ keyBytes: UnsafeRawPointer?, _ keyCount: Int,
    _ signatureBytes: UnsafeRawPointer?, _ signatureCount: Int,
    _ payloadBytes: UnsafeRawPointer?, _ payloadCount: Int
) -> Int32 {
    guard let keyBytes, let signatureBytes, let payloadBytes,
          keyCount == 32, signatureCount == 64, payloadCount >= 0 else { return 0 }
    do {
        let key = try Curve25519.Signing.PublicKey(rawRepresentation: Data(bytes: keyBytes, count: keyCount))
        return key.isValidSignature(Data(bytes: signatureBytes, count: signatureCount),
                                   for: Data(bytes: payloadBytes, count: payloadCount)) ? 1 : 0
    } catch { return 0 }
}
