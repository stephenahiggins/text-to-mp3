import Foundation

struct CloudCatalog: Decodable {
    struct Recording: Decodable { let id: String; let file: String }
    let schemaVersion: Int
    let workspaceId: String
    let stacks: [Stack]
    let recordings: [Recording]
}

// Only files in the explicitly selected folder are accessible. All provider I/O
// runs off the UI thread and uses coordination to wait for iCloud downloads.
enum CloudStore {
    static var preferences: UserDefaults { UserDefaults(suiteName: shareGroup)! }
    static var enabled: Bool { preferences.data(forKey: "cloudFolderBookmark") != nil }
    static var folderName: String { preferences.string(forKey: "cloudFolderName") ?? "iCloud Drive" }
    static func disconnect() {
        preferences.removeObject(forKey: "cloudFolderBookmark")
        preferences.removeObject(forKey: "cloudWorkspace")
        preferences.removeObject(forKey: "cloudFolderName")
    }
    static func select(_ url: URL) throws {
        guard url.startAccessingSecurityScopedResource() else { throw ShareFailure(message: "Permission to the folder was not granted. Choose it again.") }
        defer { url.stopAccessingSecurityScopedResource() }
        let marker = try JSONSerialization.jsonObject(with: read(url.appendingPathComponent("reading-stacks.json"), limit: 4096)) as? [String: Any]
        guard marker?["schemaVersion"] as? Int == 1, let workspace = marker?["workspaceId"] as? String,
              workspace.count == 32 else {
            throw ShareFailure(message: "Set up an iCloud folder in Reading Stacks on your Mac first, then choose that same folder here.")
        }
        let pending = try QueueStore.transaction { $0.items.filter { $0.status == "processing" } }
        if !pending.isEmpty && (preferences.string(forKey: "cloudWorkspace") != workspace || pending.contains { $0.delivery != "icloud" }) {
            throw ShareFailure(message: "Wait for submitted shares to finish before switching folders. You can reselect the original iCloud folder to restore access.")
        }
        let bookmark = try url.bookmarkData(options: .minimalBookmark, includingResourceValuesForKeys: nil, relativeTo: nil)
        preferences.set(bookmark, forKey: "cloudFolderBookmark")
        preferences.set(workspace, forKey: "cloudWorkspace")
        preferences.set(url.lastPathComponent, forKey: "cloudFolderName")
    }
    static func withFolder<T>(_ operation: (URL, String) throws -> T) throws -> T {
        guard let bookmark = preferences.data(forKey: "cloudFolderBookmark"),
              let workspace = preferences.string(forKey: "cloudWorkspace") else { throw ShareFailure(message: "Choose your iCloud folder first.") }
        var stale = false
        let root = try URL(resolvingBookmarkData: bookmark, options: [], relativeTo: nil, bookmarkDataIsStale: &stale)
        guard root.startAccessingSecurityScopedResource() else { throw ShareFailure(message: "Access to the iCloud folder expired. Choose the same folder again.") }
        defer { root.stopAccessingSecurityScopedResource() }
        if stale { preferences.set(try root.bookmarkData(options: .minimalBookmark, includingResourceValuesForKeys: nil, relativeTo: nil), forKey: "cloudFolderBookmark") }
        let marker = try JSONSerialization.jsonObject(with: read(root.appendingPathComponent("reading-stacks.json"), limit: 4096)) as? [String: Any]
        guard marker?["workspaceId"] as? String == workspace else { throw ShareFailure(message: "The selected folder changed. Choose your original Reading Stacks folder.") }
        return try operation(root, workspace)
    }
    static func read(_ url: URL, limit: Int) throws -> Data {
        var failure: NSError?
        var result: Result<Data, Error> = .failure(ShareFailure(message: "Waiting for iCloud to download this file."))
        NSFileCoordinator().coordinate(readingItemAt: url, options: [], error: &failure) { file in
            result = Result {
                let info = try file.resourceValues(forKeys: [.fileSizeKey, .isSymbolicLinkKey])
                guard info.isSymbolicLink != true, let size = info.fileSize, size <= limit else { throw ShareFailure(message: "iCloud file exceeds the sync limit or is invalid.") }
                let data = try Data(contentsOf: file)
                guard data.count <= limit else { throw ShareFailure(message: "iCloud file exceeds the sync limit.") }
                return data
            }
        }
        if let failure { throw failure }; return try result.get()
    }
    static func write(_ data: Data, to url: URL) throws {
        var failure: NSError?
        var result: Result<Void, Error> = .failure(ShareFailure(message: "Could not save to iCloud."))
        NSFileCoordinator().coordinate(writingItemAt: url, options: .forReplacing, error: &failure) { file in
            result = Result { try data.write(to: file, options: .atomic) }
        }
        if let failure { throw failure }; try result.get()
    }
    static func catalog(_ root: URL, _ workspace: String) throws -> CloudCatalog {
        let value = try JSONDecoder().decode(CloudCatalog.self, from: read(root.appendingPathComponent("catalog.json"), limit: 2_000_000))
        guard value.schemaVersion == 1, value.workspaceId == workspace else { throw ShareFailure(message: "Waiting for the Mac's iCloud catalog.") }
        return value
    }
    static func request(_ path: String, body: Data?, attempt: Int = 0) throws -> Data {
        try withFolder { root, workspace in
            try exchange(path, body: body, attempt: attempt, root: root, workspace: workspace)
        }
    }
    static func exchange(_ path: String, body: Data?, attempt: Int, root: URL, workspace: String) throws -> Data {
        if path == "/stacks" {
            // Allow new-stack uploads while an initial catalog is still syncing.
            do { return try JSONEncoder().encode(catalog(root, workspace).stacks) }
            catch let error as NSError where error.domain == NSCocoaErrorDomain && [4, 260].contains(error.code) {
                return try JSONEncoder().encode(QueueStore.transaction { $0.stacks })
            }
        }
        if path == "/inbox", let body,
           let payload = try JSONSerialization.jsonObject(with: body) as? [String: Any],
           let id = payload["id"] as? String, UUID(uuidString: id) != nil {
            let envelope: [String: Any] = ["schemaVersion": 1, "workspaceId": workspace, "attempt": attempt, "payload": payload]
            let data = try JSONSerialization.data(withJSONObject: envelope, options: .sortedKeys)
            guard data.count <= 27_000_000 else { throw ShareFailure(message: "Share exceeds the iCloud upload limit.") }
            try write(data, to: root.appendingPathComponent("requests/" + id + ".json"))
            return try JSONSerialization.data(withJSONObject: ["id": id, "status": "queued", "error": ""])
        }
        let id = String(path.dropFirst("/inbox/".count))
        guard path.hasPrefix("/inbox/"), UUID(uuidString: id) != nil else { throw ShareFailure(message: "Invalid iCloud request.") }
        let waiting = try JSONSerialization.data(withJSONObject: ["id": id, "status": "processing", "error": ""])
        do {
            let data = try read(root.appendingPathComponent("receipts/" + id + ".json"), limit: 16384)
            let receipt = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            guard receipt?["id"] as? String == id, receipt?["attempt"] as? Int == attempt else { return waiting }
            return data
        } catch let error as NSError where error.domain == NSCocoaErrorDomain && [4, 260].contains(error.code) {
            // A local write may have been interrupted before it reached the provider.
            let requestURL = root.appendingPathComponent("requests/" + id + ".json")
            if !FileManager.default.fileExists(atPath: requestURL.path) { throw ShareHTTPError(status: 404, message: "Share needs to be sent again.") }
            return waiting
        }
    }
    static func removeRequest(_ id: String) throws {
        guard UUID(uuidString: id) != nil else { return }
        try withFolder { root, _ in
            let url = root.appendingPathComponent("requests/" + id + ".json")
            var error: NSError?
            NSFileCoordinator().coordinate(writingItemAt: url, options: .forDeleting, error: &error) { file in
                try? FileManager.default.removeItem(at: file)
            }
            if let error { throw error }
        }
    }
    static func nextRecording() throws -> [String: String]? {
        guard enabled else { return nil }
        return try withFolder { root, workspace in
            let imported = Set(preferences.stringArray(forKey: "cloudImported." + workspace) ?? [])
            let available: CloudCatalog
            do { available = try catalog(root, workspace) }
            catch let error as NSError where error.domain == NSCocoaErrorDomain && [4, 260].contains(error.code) { return nil }
            for recording in available.recordings where !imported.contains(recording.id) {
                guard recording.id.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil,
                      recording.file == recording.id + ".stack" else { throw ShareFailure(message: "Invalid iCloud recording name.") }
                let content = try read(root.appendingPathComponent("recordings/" + recording.file), limit: 150_000_000)
                let local = try QueueStore.root().appendingPathComponent("cloud-recording.stack")
                try content.write(to: local, options: [.atomic, .completeFileProtection])
                return ["id": recording.id, "uri": local.absoluteString, "name": recording.file, "workspace": workspace]
            }
            return nil
        }
    }
    static func acknowledgeRecording(_ id: String, workspace: String) {
        let key = "cloudImported." + workspace
        var ids = preferences.stringArray(forKey: key) ?? []
        if !ids.contains(id) { ids.append(id); preferences.set(ids, forKey: key) }
        if let root = try? QueueStore.root() { try? FileManager.default.removeItem(at: root.appendingPathComponent("cloud-recording.stack")) }
    }
}
