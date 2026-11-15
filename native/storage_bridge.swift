import Foundation

private func storageError(_ message: String) -> NSError {
    NSError(domain: "org.projectleap.storage", code: 1,
            userInfo: [NSLocalizedDescriptionKey: message])
}

// One coordinator and one caller-owned error buffer per invocation. This entry
// has no UI or main-thread dependency and runs on the maintenance worker.
@_cdecl("leap_storage_publish_directory")
public func leapStoragePublishDirectory(
    _ source: UnsafePointer<CChar>?,
    _ destination: UnsafePointer<CChar>?,
    _ errorBuffer: UnsafeMutablePointer<CChar>?,
    _ errorCapacity: UInt
) -> Int32 {
    func fail(_ message: String) -> Int32 {
        if let errorBuffer, errorCapacity > 0 {
            let bytes = Array(message.utf8)
            var count = min(bytes.count, Int(min(errorCapacity - 1, UInt(Int.max))))
            while count > 0, count < bytes.count, bytes[count] & 0xc0 == 0x80 { count -= 1 }
            for (index, byte) in bytes.prefix(count).enumerated() {
                errorBuffer[index] = CChar(bitPattern: byte)
            }
            errorBuffer[count] = 0
        }
        return 1
    }
    if let errorBuffer, errorCapacity > 0 { errorBuffer[0] = 0 }
    guard let source, let destination,
          let sourcePath = String(validatingCString: source),
          let destinationPath = String(validatingCString: destination),
          (sourcePath as NSString).isAbsolutePath,
          (destinationPath as NSString).isAbsolutePath,
          sourcePath != destinationPath else {
        return fail("Migration publication requires two distinct absolute directory paths.")
    }
    let sourceURL = URL(fileURLWithPath: sourcePath, isDirectory: true)
    let destinationURL = URL(fileURLWithPath: destinationPath, isDirectory: true)
    let coordinator = NSFileCoordinator(filePresenter: nil)
    var coordinationError: NSError?
    var operationError: Error?
    var moved = false
    coordinator.coordinate(writingItemAt: sourceURL, options: .forMoving,
                           writingItemAt: destinationURL, options: .forReplacing,
                           error: &coordinationError) { currentSource, currentDestination in
        do {
            // The fixed settings pointer must continue to name the user's
            // requested destination even if another presenter moves an item.
            guard currentDestination.standardizedFileURL == destinationURL.standardizedFileURL else {
                throw storageError("The migration destination changed while waiting for coordinated access.")
            }
            let manager = FileManager()
            if manager.fileExists(atPath: currentDestination.path) {
                let values = try currentDestination.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
                guard values.isDirectory == true, values.isSymbolicLink != true,
                      try manager.contentsOfDirectory(atPath: currentDestination.path).isEmpty else {
                    throw storageError("The migration destination must be an empty directory.")
                }
                // rmdir is non-recursive: an uncoordinated writer that adds a
                // child after the check makes this fail instead of losing data.
                guard rmdir(currentDestination.path) == 0 else {
                    throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno))
                }
            }
            coordinator.item(at: currentSource, willMoveTo: currentDestination)
            try manager.moveItem(at: currentSource, to: currentDestination)
            coordinator.item(at: currentSource, didMoveTo: currentDestination)
            moved = true
        } catch {
            operationError = error
        }
    }
    if moved { return 0 }
    return fail((operationError ?? coordinationError ?? storageError("Coordinated directory publication did not run.")).localizedDescription)
}
