// Publisher/development utility only; never included in the application.
import Foundation
import CryptoKit

let args = CommandLine.arguments
if args.count == 3 && args[1] == "generate" {
    let key = Curve25519.Signing.PrivateKey()
    let url = URL(fileURLWithPath: args[2])
    try key.rawRepresentation.write(to: url, options: .withoutOverwriting)
    try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: args[2])
    print(key.publicKey.rawRepresentation.base64EncodedString())
} else if args.count == 4 && args[1] == "sign" {
    let key = try Curve25519.Signing.PrivateKey(rawRepresentation: Data(contentsOf: URL(fileURLWithPath: args[2])))
    let payload = try Data(contentsOf: URL(fileURLWithPath: args[3]))
    print(try key.signature(for: payload).base64EncodedString())
} else {
    fatalError("Usage: signer generate PRIVATE_KEY_PATH | signer sign PRIVATE_KEY_PATH PAYLOAD_PATH")
}
