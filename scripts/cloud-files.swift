// File-provider-aware I/O for the local Python service. No Apple credentials.
import Foundation

func read(_ url: URL, limit: Int) throws -> Data {
    if (try? url.resourceValues(forKeys: [.isUbiquitousItemKey]).isUbiquitousItem) == true {
        try FileManager.default.startDownloadingUbiquitousItem(at: url)
    }
    var coordinationError: NSError?
    var result: Result<Data, Error> = .failure(NSError(domain: "ReadingStacks", code: 1))
    NSFileCoordinator().coordinate(readingItemAt: url, options: [], error: &coordinationError) { target in
        result = Result {
            let size = try target.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
            guard size <= limit else { throw NSError(domain: "File exceeds sync limit", code: 2) }
            let data = try Data(contentsOf: target)
            guard data.count <= limit else { throw NSError(domain: "File exceeds sync limit", code: 2) }
            return data
        }
    }
    if let coordinationError { throw coordinationError }
    return try result.get()
}
do {
    let args = CommandLine.arguments
    let url = URL(fileURLWithPath: args[2])
    switch args[1] {
    case "read":
        FileHandle.standardOutput.write(try read(url, limit: Int(args[3])!))
    case "write":
        let source = URL(fileURLWithPath: args[3])
        var error: NSError?
        var failure: Error?
        NSFileCoordinator().coordinate(writingItemAt: url, options: .forReplacing, error: &error) { target in
            do { try Data(contentsOf: source, options: .mappedIfSafe).write(to: target, options: .atomic) }
            catch { failure = error }
        }
        if let error { throw error }; if let failure { throw failure }
    case "list":
        let files = try FileManager.default.contentsOfDirectory(at: url, includingPropertiesForKeys: [.isUbiquitousItemKey])
        var names: [String] = []
        for file in files {
            var name = file.lastPathComponent
            if name.hasPrefix("."), name.hasSuffix(".icloud") { name = String(name.dropFirst().dropLast(7)) }
            let original = url.appendingPathComponent(name)
            if name.hasSuffix(".json"), (try? original.resourceValues(forKeys: [.isUbiquitousItemKey]).isUbiquitousItem) == true {
                try? FileManager.default.startDownloadingUbiquitousItem(at: original)
            }
            names.append(name)
        }
        FileHandle.standardOutput.write(try JSONEncoder().encode(names))
    default: throw NSError(domain: "Unknown file operation", code: 3)
    }
} catch {
    FileHandle.standardError.write(Data(error.localizedDescription.utf8)); exit(1)
}
