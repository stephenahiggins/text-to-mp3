import Foundation
import SwiftUI
import Security
import Network
import Darwin

struct Stack: Codable, Identifiable { let id: String; let name: String }
struct ShareFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}
let shareGroup = "group.com.articlestacks.mobile.sharing"

// App Group keychain membership grants access to both signed targets.
enum ConnectionStore {
    static var query: [String: Any] { [kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "ReadingStacks.ShareConnection", kSecAttrAccount as String: "mac",
        kSecAttrAccessGroup as String: shareGroup] }
    static func read() -> String {
        func get(_ query: [String: Any]) -> String {
            var q = query; q[kSecReturnData as String] = true
            var result: CFTypeRef?
            guard SecItemCopyMatching(q as CFDictionary, &result) == errSecSuccess,
                  let data = result as? Data else { return "" }
            return String(data: data, encoding: .utf8) ?? ""
        }
        let shared = get(query)
        if !shared.isEmpty { return shared }
        var legacy = query; legacy.removeValue(forKey: kSecAttrAccessGroup as String)
        let old = get(legacy)
        if !old.isEmpty { try? write(old) }
        return old
    }
    static func write(_ value: String) throws {
        let data = Data(value.utf8)
        let status = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if status == errSecSuccess { return }
        var q = query
        q[kSecValueData as String] = data
        q[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        guard status == errSecItemNotFound, SecItemAdd(q as CFDictionary, nil) == errSecSuccess else {
            throw ShareFailure(message: "Could not store the Mac connection. Check App Group signing.")
        }
    }
}

struct QueuedShare: Codable, Identifiable {
    var id = UUID().uuidString.lowercased()
    var title: String
    var url: String
    var file: String?
    var stackId: String
    var newStackName: String?
    var newStackId: String?
    var status = "waiting"
    var error = ""
    var retry = false
    var created = Date()
    var destinationID: String {
        newStackName == nil ? stackId : newStackId ?? "shared-" + id
    }
    var editable: Bool { status == "waiting" || status == "error" }
    var label: String {
        switch status {
        case "processing": return "Processing on Mac / awaiting confirmation"
        case "complete": return "Added to stack"
        case "error": return "Needs attention"
        default: return destinationID.isEmpty ? "Choose a stack" : "Waiting for Mac"
        }
    }
}
struct QueueState: Codable {
    var items: [QueuedShare] = []
    var stacks: [Stack] = []
    var lastStack = ""
    var pendingPresentation: String?

    var destinations: [Stack] {
        var result = stacks
        var known = Set(stacks.map(\.id))
        for item in items where item.status != "complete" {
            if let name = item.newStackName, known.insert(item.destinationID).inserted {
                result.append(Stack(id: item.destinationID, name: name))
            }
        }
        return result
    }

    var suggestedDestination: String {
        let previous = lastStack.isEmpty ? items.last?.destinationID : lastStack
        return destinations.first { $0.id == previous }?.id ?? "new"
    }

    func canRenameDraft(_ id: String) -> Bool {
        guard !stacks.contains(where: { $0.id == id }) else { return false }
        let shares = items.filter { $0.destinationID == id }
        return shares.contains { $0.newStackName != nil } && shares.allSatisfy(\.editable)
    }

    mutating func renameDraft(_ id: String, to proposedName: String) throws {
        let name = proposedName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard (1...200).contains(name.unicodeScalars.count) else {
            throw ShareFailure(message: "Enter a stack name of 1–200 characters.")
        }
        guard canRenameDraft(id) else {
            throw ShareFailure(message: "This stack can only be renamed before it syncs to your Mac.")
        }
        for index in items.indices where items[index].destinationID == id && items[index].newStackName != nil {
            guard items[index].newStackName != name else { continue }
            if items[index].status == "error" || items[index].retry {
                // A previous submission may have reserved this receipt ID for the old payload.
                let oldID = items[index].id
                items[index].id = UUID().uuidString.lowercased()
                if pendingPresentation == oldID { pendingPresentation = items[index].id }
                items[index].newStackId = id
                items[index].retry = false
            }
            items[index].newStackName = name
        }
    }

    func setDestination(_ destination: String, for item: inout QueuedShare) throws {
        if destination.isEmpty || stacks.contains(where: { $0.id == destination }) {
            item.stackId = destination
            item.newStackName = nil
            item.newStackId = nil
        } else if let draft = destinations.first(where: { $0.id == destination }) {
            item.stackId = ""
            item.newStackName = draft.name
            item.newStackId = draft.id
        } else {
            throw ShareFailure(message: "This stack is no longer available. Choose another stack.")
        }
    }
}

// A cross-process lock protects atomic JSON replacement; PDF files are immutable.
enum QueueStore {
    static func root() throws -> URL {
        #if QUEUE_STORAGE_TESTS
        return URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
        #else
        guard let root = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: shareGroup) else {
            throw ShareFailure(message: "Shared storage unavailable. Reinstall the app with App Groups enabled.")
        }
        return root
        #endif
    }
    static func lock(_ name: String, nonblocking: Bool = false) throws -> Int32 {
        let fd = Darwin.open(try root().appendingPathComponent(name).path, O_CREAT | O_RDWR, 0o600)
        guard fd >= 0 else { throw ShareFailure(message: "Could not open queue storage.") }
        if flock(fd, LOCK_EX | (nonblocking ? LOCK_NB : 0)) != 0 {
            Darwin.close(fd)
            throw ShareFailure(message: "Another share is syncing. Try again shortly.")
        }
        return fd
    }
    static func transaction<T>(_ change: (inout QueueState) throws -> T) throws -> T {
        let fd = try lock("queue.lock"); defer { Darwin.close(fd) }
        let path = try root().appendingPathComponent("queue.json")
        var state = FileManager.default.fileExists(atPath: path.path) ? try JSONDecoder().decode(QueueState.self, from: Data(contentsOf: path)) : QueueState()
        let result = try change(&state)
        try JSONEncoder().encode(state).write(to: path, options: [.atomic, .completeFileProtection])
        return result
    }
    static func update(_ item: QueuedShare) throws {
        try transaction { state in
            if let index = state.items.firstIndex(where: { $0.id == item.id }) { state.items[index] = item }
        }
    }
    @discardableResult
    static func enqueue(title: String, url: String, pdf: Data?, stackId: String, newStackName: String? = nil) throws -> String {
        try transaction { state in
            guard state.items.count < 500 else { throw ShareFailure(message: "Queue is full. Remove completed items first.") }
            var item = QueuedShare(title: String(title.prefix(500)), url: url, stackId: stackId, newStackName: newStackName)
            if newStackName == nil && stackId.hasPrefix("shared-") {
                try state.setDestination(stackId, for: &item)
            }
            if let pdf {
                guard pdf.count <= 20_000_000 else { throw ShareFailure(message: "PDF exceeds 20 MB.") }
                let root = try root()
                let total = try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: [.fileSizeKey]).filter { $0.pathExtension == "pdf" }.reduce(0) { sum, file in sum + ((try? file.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0) }
                guard total + pdf.count <= 200_000_000 else { throw ShareFailure(message: "PDF queue is full (200 MB). Send or delete items first.") }
                item.file = item.id + ".pdf"
                try pdf.write(to: root.appendingPathComponent(item.file!), options: [.atomic, .completeFileProtection])
            }
            state.items.append(item); state.lastStack = item.destinationID
            state.pendingPresentation = item.id
            return item.id
        }
    }
}

final class NoRedirect: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}
struct Receipt: Decodable { let id: String; let status: String; let error: String }
struct ShareHTTPError: LocalizedError {
    let status: Int; let message: String
    var errorDescription: String? { message }
}

struct NearbyMac: Identifiable {
    let id: String
    let name: String
    let base: String
}

// Bonjour carries only an identity and endpoint, never pairing credentials.
@MainActor final class MacDiscovery: NSObject, @preconcurrency NetServiceBrowserDelegate, @preconcurrency NetServiceDelegate {
    private let browser = NetServiceBrowser()
    private var services: [NetService] = []
    private var resolved: [String: NearbyMac] = [:]
    var changed: (([NearbyMac]) -> Void)?
    var failed: ((String) -> Void)?
    private var running = false
    func start() {
        guard !running else { return }
        running = true
        browser.delegate = self
        browser.searchForServices(ofType: "_readingstacks._tcp.", inDomain: "local.")
    }
    func stop() {
        browser.stop(); running = false
        services.forEach { $0.stop() }
        services.removeAll(); resolved.removeAll(); changed?([])
    }
    private func key(_ service: NetService) -> String { service.name + "." + service.domain }
    func netServiceBrowser(_ browser: NetServiceBrowser, didFind service: NetService, moreComing: Bool) {
        services.append(service); service.delegate = self; service.resolve(withTimeout: 8)
    }
    func netServiceBrowser(_ browser: NetServiceBrowser, didRemove service: NetService, moreComing: Bool) {
        services.removeAll { key($0) == key(service) }
        resolved.removeValue(forKey: key(service)); publish()
    }
    func netServiceBrowser(_ browser: NetServiceBrowser, didNotSearch errorDict: [String: NSNumber]) {
        running = false
        failed?("Allow Local Network access for Reading Stacks in iPhone Settings, then reopen the app.")
    }
    func netServiceDidResolveAddress(_ service: NetService) {
        guard running, let host = service.hostName, service.port > 0, let txt = service.txtRecordData(),
              let data = NetService.dictionary(fromTXTRecord: txt)["id"],
              let id = String(data: data, encoding: .utf8), !id.isEmpty else { return }
        var parts = URLComponents(); parts.scheme = "http"; parts.host = host; parts.port = service.port
        guard let base = parts.url?.absoluteString else { return }
        let name = host.replacingOccurrences(of: ".local.", with: "").replacingOccurrences(of: ".local", with: "")
        resolved[key(service)] = NearbyMac(id: id, name: name, base: base)
        publish()
    }
    private func publish() { changed?(resolved.values.sorted { $0.name < $1.name }) }
}

@MainActor final class ShareQueue: ObservableObject {
    static let shared = ShareQueue()
    @Published var state = QueueState()
    @Published var message = ""
    @Published var busy = false
    @Published var wifi = false
    @Published var nearbyMacs: [NearbyMac] = []
    @Published var connectionStatus = "Looking for your Mac on Wi-Fi…"
    @Published var pairingCode = ""
    private let discovery = MacDiscovery()
    private let preferences = UserDefaults(suiteName: shareGroup)!
    private var selectedMacID: String?
    private let monitor = NWPathMonitor()
    private var timer: Timer?
    private var active = false
    init() {
        discovery.changed = { [weak self] macs in
            guard let self else { return }
            self.nearbyMacs = macs
            if self.active { Task { await self.sync() } }
        }
        discovery.failed = { [weak self] message in self?.connectionStatus = message }
        reload()
        monitor.pathUpdateHandler = { [weak self] path in
            let usable = path.status == .satisfied && path.usesInterfaceType(.wifi)
            Task { @MainActor in
                guard let self else { return }
                self.wifi = usable
                if usable && self.active { self.discovery.start(); await self.sync() }
                if !usable { self.discovery.stop() }
            }
        }
        monitor.start(queue: DispatchQueue(label: "ReadingStacks.network"))
    }
    func setActive(_ value: Bool) {
        active = value; timer?.invalidate(); timer = nil
        if !value { discovery.stop() }
        if value {
            discovery.start()
            reload()
            Task { await sync() }
            timer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { _ in
                Task { @MainActor in await ShareQueue.shared.sync() }
            }
        }
    }
    func reload() {
        do { state = try QueueStore.transaction { $0 } }
        catch { message = error.localizedDescription }
    }
    func chooseMac(_ mac: NearbyMac) {
        preferences.removeObject(forKey: "pairedMacID")
        selectedMacID = mac.id
        Task { await sync() }
    }
    private func discoveredConnection() async throws -> Bool {
        let savedID = preferences.string(forKey: "pairedMacID")
        let targetID = savedID ?? selectedMacID
        let mac: NearbyMac?
        if let targetID { mac = nearbyMacs.first { $0.id == targetID } }
        else { mac = nearbyMacs.count == 1 ? nearbyMacs.first : nil }
        let existing = ConnectionStore.read()
        // Old manual pairings continue to work without needing to approve again.
        if !existing.isEmpty {
            if let mac, savedID != nil, var link = URLComponents(string: mac.base),
               let token = URLComponents(string: existing)?.fragment {
                link.fragment = token
                if let value = link.string { try ConnectionStore.write(value) }
            }
            return true
        }
        guard let mac else {
            connectionStatus = targetID != nil ? "Waiting for your paired Mac. Keep Reading Stacks running on it." : nearbyMacs.count > 1 ? "Choose your Mac below." : "Looking for your Mac. Keep Reading Stacks running on it and allow Local Network access."
            return false
        }
        selectedMacID = mac.id
        let secretKey = "pairingRequest." + mac.id
        let secret = preferences.string(forKey: secretKey) ?? (UUID().uuidString + UUID().uuidString).replacingOccurrences(of: "-", with: "").lowercased()
        preferences.set(secret, forKey: secretKey)
        var request = URLRequest(url: URL(string: mac.base + "/pair")!, timeoutInterval: 8)
        request.httpMethod = "POST"; request.allowsCellularAccess = false
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["secret": secret, "name": "Reading Stacks iPhone"])
        let config = URLSessionConfiguration.ephemeral
        config.allowsCellularAccess = false
        let session = URLSession(configuration: config, delegate: NoRedirect(), delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        let (data, response) = try await session.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw ShareFailure(message: "Could not connect to your Mac. Try again shortly.") }
        struct PairingReply: Decodable { let status: String; let code: String; let token: String? }
        let reply = try JSONDecoder().decode(PairingReply.self, from: data)
        pairingCode = reply.code
        if reply.status == "approved", let token = reply.token {
            try ConnectionStore.write(mac.base + "#" + token)
            preferences.set(mac.id, forKey: "pairedMacID")
            preferences.removeObject(forKey: secretKey)
            pairingCode = ""; connectionStatus = "Connected to " + mac.name
            return true
        }
        connectionStatus = reply.status == "rejected" ? "Connection declined on your Mac. Tap Retry connection to ask again." : "Found \(mac.name). Approve this code in Reading Stacks on your Mac once."
        return false
    }
    func retryConnection() {
        if let id = selectedMacID ?? preferences.string(forKey: "pairedMacID") {
            preferences.removeObject(forKey: "pairingRequest." + id)
        }
        Task { await sync() }
    }
    func request(_ path: String, body: Data? = nil, connection: String? = nil) async throws -> Data {
        guard wifi else { throw ShareFailure(message: "Saved items will wait for Wi-Fi and your Mac.") }
        guard var parts = URLComponents(string: (connection ?? ConnectionStore.read()).trimmingCharacters(in: .whitespacesAndNewlines)),
              ["http", "https"].contains(parts.scheme ?? ""), parts.host != nil,
              parts.user == nil, parts.password == nil, let token = parts.fragment, token.count >= 32 else {
            throw ShareFailure(message: "Connect to your Mac below. Your queued items are safe.")
        }
        parts.fragment = nil; parts.query = nil; parts.path = path
        guard let endpoint = parts.url else { throw ShareFailure(message: "Invalid connection link.") }
        var request = URLRequest(url: endpoint, timeoutInterval: 8)
        request.allowsCellularAccess = false
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let body {
            request.httpMethod = "POST"; request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let config = URLSessionConfiguration.ephemeral
        config.allowsCellularAccess = false; config.timeoutIntervalForResource = 30
        let session = URLSession(configuration: config, delegate: NoRedirect(), delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw ShareFailure(message: "No response from Mac.") }
        guard (200..<300).contains(response.statusCode) else {
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: String])?["detail"]
            throw ShareHTTPError(status: response.statusCode, message: detail ?? "Could not contact your Mac.")
        }
        return data
    }
    func connect(_ link: String) async {
        guard !busy else { return }
        busy = true; defer { busy = false }
        do {
            let stacks = try JSONDecoder().decode([Stack].self, from: await request("/stacks", connection: link))
            try ConnectionStore.write(link.trimmingCharacters(in: .whitespacesAndNewlines))
            preferences.removeObject(forKey: "pairedMacID")
            selectedMacID = nil; pairingCode = ""
            connectionStatus = "Connected to your Mac"
            try QueueStore.transaction { $0.stacks = stacks }
            message = "Connected to Mac. Tap Send now to sync."; reload()
        } catch { message = error.localizedDescription }
    }
    func sync() async {
        guard !busy else { return }
        reload()
        guard wifi else { message = "Waiting for Wi-Fi and your Mac."; return }
        busy = true; defer { busy = false; reload() }
        do {
            guard try await discoveredConnection() else { return }
            let fd = try QueueStore.lock("sync.lock", nonblocking: true); defer { Darwin.close(fd) }
            let stacks = try JSONDecoder().decode([Stack].self, from: await request("/stacks"))
            try QueueStore.transaction { $0.stacks = stacks }; reload(); message = ""; connectionStatus = "Connected to your Mac"
            for var item in state.items where item.status != "complete" && item.status != "error" && (!item.stackId.isEmpty || item.newStackName != nil) {
                if item.status == "processing" {
                    do {
                        let receipt = try JSONDecoder().decode(Receipt.self, from: await request("/inbox/" + item.id))
                        try apply(receipt, to: &item)
                        continue
                    } catch let error as ShareHTTPError where error.status == 404 { /* lost upload: resend same ID */ }
                }
                var payload: [String: Any] = ["id": item.id, "stackId": item.stackId, "url": item.url, "title": item.title, "retry": item.retry]
                if let name = item.newStackName { payload["newStackName"] = name }
                if let id = item.newStackId { payload["newStackId"] = id }
                if let file = item.file { payload["pdf"] = try Data(contentsOf: QueueStore.root().appendingPathComponent(file)).base64EncodedString() }
                // Persist the uncertain state before POST. Never change destination after an ambiguous response.
                item.status = "processing"; try QueueStore.update(item); reload()
                do {
                    let receipt = try JSONDecoder().decode(Receipt.self, from: await request("/inbox", body: JSONSerialization.data(withJSONObject: payload)))
                    try apply(receipt, to: &item)
                } catch let error as ShareHTTPError where [400, 409, 413, 507].contains(error.status) {
                    item.status = "error"; item.error = error.localizedDescription
                    try QueueStore.update(item)
                }
            }
        } catch let error as ShareHTTPError where error.status == 401 {
            // Revoked credentials require a fresh approval, never a silent grant.
            try? ConnectionStore.write("")
            connectionStatus = "Mac connection was reset. Approve the next connection request on your Mac."
            message = error.localizedDescription
        } catch { message = error.localizedDescription }
    }
    private func apply(_ receipt: Receipt, to item: inout QueuedShare) throws {
        item.status = receipt.status == "complete" ? "complete" : receipt.status == "error" ? "error" : "processing"
        item.error = receipt.error; item.retry = false
        try QueueStore.transaction { state in
            if let index = state.items.firstIndex(where: { $0.id == item.id }) { state.items[index] = item }
            if item.status == "complete", let name = item.newStackName,
               !state.stacks.contains(where: { $0.id == item.destinationID }) {
                state.stacks.append(Stack(id: item.destinationID, name: name))
            }
        }
        if item.status == "complete", let file = item.file {
            try? FileManager.default.removeItem(at: QueueStore.root().appendingPathComponent(file))
        }
    }
    func destination(_ item: QueuedShare, _ stack: String) {
        guard item.editable, !busy else { return }
        do {
            let fd = try QueueStore.lock("sync.lock", nonblocking: true); defer { Darwin.close(fd) }
            try QueueStore.transaction { state in
                guard let i = state.items.firstIndex(where: { $0.id == item.id && $0.editable }),
                      state.items[i].destinationID != stack else { return }
                var updated = state.items[i]
                try state.setDestination(stack, for: &updated)
                // Only known failures may be redirected; the failed receipt remains on Mac.
                if state.items[i].status == "error" {
                    updated.id = UUID().uuidString.lowercased()
                    updated.retry = false
                    if state.pendingPresentation == item.id { state.pendingPresentation = updated.id }
                }
                updated.status = "waiting"
                updated.error = ""
                state.items[i] = updated
            }; reload()
        } catch { message = error.localizedDescription }
    }
    func retry(_ item: QueuedShare) {
        do { let fd = try QueueStore.lock("sync.lock", nonblocking: true); defer { Darwin.close(fd) }; var item = item; item.status = "waiting"; item.retry = true; item.error = ""; try QueueStore.update(item); reload(); Task { await sync() } }
        catch { message = error.localizedDescription }
    }
    func renameDraft(_ id: String, to name: String) {
        do {
            let fd = try QueueStore.lock("sync.lock", nonblocking: true); defer { Darwin.close(fd) }
            try QueueStore.transaction { try $0.renameDraft(id, to: name) }
            message = ""
            reload()
        } catch {
            message = error.localizedDescription
            reload()
        }
    }
    func remove(_ item: QueuedShare) {
        guard !busy, item.status != "processing" else { return }
        do {
            let fd = try QueueStore.lock("sync.lock", nonblocking: true); defer { Darwin.close(fd) }
            let file = try QueueStore.transaction { state -> String? in
                guard let index = state.items.firstIndex(where: { $0.id == item.id && $0.status != "processing" }) else { return nil }
                let removed = state.items.remove(at: index)
                if state.pendingPresentation == item.id { state.pendingPresentation = nil }
                return removed.file
            }
            if let file { try? FileManager.default.removeItem(at: QueueStore.root().appendingPathComponent(file)) }
            reload()
        } catch { message = error.localizedDescription }
    }
}

struct ShareQueueView: View {
    var focusedItemID: String? = nil
    @ObservedObject var queue = ShareQueue.shared
    @State private var connection = ""
    @State private var renamingStackID: String?
    @State private var stackName = ""
    @State private var showingRename = false
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        let sorted = queue.state.items.sorted {
            if $0.id == focusedItemID { return $1.id != focusedItemID }
            if $1.id == focusedItemID { return false }
            return $0.created > $1.created
        }
        let pending = Dictionary(grouping: sorted.filter { $0.status != "complete" }, by: \.destinationID)
        let completed = sorted.filter { $0.status == "complete" && $0.id != focusedItemID }
        let names = Dictionary(uniqueKeysWithValues: queue.state.destinations.map { ($0.id, $0.name) })
        return NavigationStack {
            Form {
                if let item = sorted.first(where: { $0.id == focusedItemID && $0.status == "complete" }) {
                    Section("Shared article") { SharedArticleRow(item: item, showStack: true) }
                }
                Section {
                    Text("Links and PDFs stay on this iPhone until your Mac adds them to a stack. Open Reading Stacks on home Wi-Fi to send them.")
                    Button("Send now") { Task { await queue.sync() } }.disabled(queue.busy || !queue.wifi)
                    if queue.busy { ProgressView("Syncing…") }
                    if !queue.message.isEmpty { Text(queue.message).font(.footnote) }
                }
                if sorted.isEmpty {
                    Text("Share a webpage or PDF to Add to stack to save it here.")
                }
                ForEach(pending.keys.sorted {
                    (names[$0] ?? "", $0) < (names[$1] ?? "", $1)
                }, id: \.self) { id in
                    let articles = pending[id, default: []]
                    Section {
                        ForEach(articles) { item in SharedArticleRow(item: item) }
                    } header: {
                        HStack(spacing: 0) {
                            Text(names[id] ?? (id.isEmpty ? "No stack selected" : "Unavailable stack"))
                                .accessibilityAddTraits(.isHeader)
                            if let name = names[id], queue.state.canRenameDraft(id) {
                                Button {
                                    renamingStackID = id
                                    stackName = name
                                    showingRename = true
                                } label: {
                                    Image(systemName: "pencil")
                                        .foregroundStyle(Color.accentColor)
                                        .frame(minWidth: 44, minHeight: 44)
                                        .contentShape(Rectangle())
                                }
                                .buttonStyle(.plain)
                                .accessibilityLabel("Rename \(name)")
                                .disabled(queue.busy)
                            }
                        }
                    } footer: {
                        Text("\(articles.count) pending \(articles.count == 1 ? "article" : "articles")")
                    }
                }
                if !completed.isEmpty {
                    Section("Added to Mac stacks") {
                        ForEach(completed) { item in SharedArticleRow(item: item, showStack: true) }
                    }
                }
                Section("Mac connection") {
                    Text(queue.connectionStatus).font(.footnote)
                    if !queue.pairingCode.isEmpty {
                        Text(queue.pairingCode).font(.title.monospacedDigit()).accessibilityLabel("Pairing code " + queue.pairingCode)
                        Button("Retry connection") { queue.retryConnection() }.disabled(queue.busy)
                    }
                    if queue.nearbyMacs.count > 1 && ConnectionStore.read().isEmpty {
                        ForEach(queue.nearbyMacs) { mac in
                            Button(mac.name) { queue.chooseMac(mac) }.disabled(queue.busy)
                        }
                    }
                    DisclosureGroup("Manual connection") {
                        SecureField("Connection link", text: $connection).textInputAutocapitalization(.never).autocorrectionDisabled()
                        Button("Connect with link") { Task { await queue.connect(connection) } }.disabled(queue.busy || !queue.wifi || connection.isEmpty)
                    }
                }
            }.navigationTitle(focusedItemID == nil ? "Saved shares" : "Shared article")
                .alert("Rename stack", isPresented: $showingRename) {
                    TextField("Stack name", text: $stackName)
                    Button("Cancel", role: .cancel) {}
                    Button("Save") {
                        if let renamingStackID {
                            queue.renameDraft(renamingStackID, to: stackName)
                        }
                    }
                    .disabled(!(1...200).contains(stackName.trimmingCharacters(in: .whitespacesAndNewlines).unicodeScalars.count))
                }
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
                .task(id: focusedItemID) {
                    queue.reload()
                    if let focusedItemID {
                        do {
                            try QueueStore.transaction { state in
                                if state.pendingPresentation == focusedItemID { state.pendingPresentation = nil }
                            }
                        } catch { queue.message = error.localizedDescription }
                    }
                }
        }
    }
}

struct SharedArticleRow: View {
    let item: QueuedShare
    var showStack = false
    @ObservedObject var queue = ShareQueue.shared
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(item.title).font(.headline)
            Text(item.label).font(.subheadline)
            if showStack && !item.editable,
               let name = queue.state.destinations.first(where: { $0.id == item.destinationID })?.name ?? item.newStackName {
                Text(name).font(.footnote)
            }
            if !item.error.isEmpty { Text(item.error).foregroundStyle(.red).font(.footnote) }
            if item.editable {
                Picker("Stack", selection: Binding(get: { item.destinationID }, set: { queue.destination(item, $0) })) {
                    Text("Choose later").tag("")
                    ForEach(queue.state.destinations) { Text($0.name).tag($0.id) }
                    if !item.destinationID.isEmpty && !queue.state.destinations.contains(where: { $0.id == item.destinationID }) {
                        Text("Unavailable stack").tag(item.destinationID)
                    }
                }.disabled(queue.busy)
            }
            if item.status == "error" { Button("Retry") { queue.retry(item) }.disabled(queue.busy) }
            if item.status != "processing" { Button(item.status == "complete" ? "Clear from queue" : "Delete saved share", role: .destructive) { queue.remove(item) }.disabled(queue.busy) }
        }
    }
}
