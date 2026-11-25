import UIKit
import SwiftUI
import UniformTypeIdentifiers

@MainActor final class ShareModel: ObservableObject {
    @Published var selected = "new"
    @Published var newStackName = ""
    @Published var title = ""
    @Published var url = ""
    @Published var pdf: Data?
    @Published var error = ""
    @Published var loading = true
    @Published var saved = false
    @Published var savedItemID: String?
    var context: NSExtensionContext?
    func start(_ context: NSExtensionContext?) {
        self.context = context
        _ = ConnectionStore.read() // Migrate an existing extension-private pairing.
        ShareQueue.shared.reload()
        selected = ShareQueue.shared.state.suggestedDestination
        Task {
            defer { loading = false }
            do {
                let providers = (context?.inputItems as? [NSExtensionItem] ?? []).flatMap { $0.attachments ?? [] }
                // Safari may supply the PDF itself instead of its remote address.
                if let provider = providers.first(where: { $0.hasItemConformingToTypeIdentifier(UTType.pdf.identifier) }) {
                    pdf = try await withCheckedThrowingContinuation { continuation in
                        provider.loadFileRepresentation(forTypeIdentifier: UTType.pdf.identifier) { file, error in
                            do {
                                if let error { throw error }
                                guard let file else { throw ShareFailure(message: "Unable to read PDF.") }
                                let size = try file.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
                                guard size <= 20_000_000 else { throw ShareFailure(message: "PDF exceeds 20 MB.") }
                                // Provider's temporary URL is valid only inside this callback.
                                continuation.resume(returning: try Data(contentsOf: file))
                            } catch { continuation.resume(throwing: error) }
                        }
                    }
                    title = provider.suggestedName ?? "PDF document"
                } else {
                    for provider in providers where provider.hasItemConformingToTypeIdentifier(UTType.url.identifier) {
                        let value: NSSecureCoding? = try await withCheckedThrowingContinuation { continuation in
                            provider.loadItem(forTypeIdentifier: UTType.url.identifier, options: nil) { value, error in
                                if let error { continuation.resume(throwing: error) }
                                else { continuation.resume(returning: value) }
                            }
                        }
                        let candidate = (value as? URL) ?? (value as? String).flatMap(URL.init(string:))
                        if let candidate, ["http", "https"].contains(candidate.scheme?.lowercased() ?? ""), candidate.host != nil, candidate.user == nil {
                            url = candidate.absoluteString; title = candidate.absoluteString; break
                        }
                    }
                    guard !url.isEmpty else { throw ShareFailure(message: "Share one webpage link or PDF file.") }
                }
            } catch { self.error = error.localizedDescription }
        }
    }
    func save() {
        guard !loading, !saved, selected != "new" || (1...200).contains(newStackName.trimmingCharacters(in: .whitespacesAndNewlines).count) else { return }
        do {
            savedItemID = try QueueStore.enqueue(title: title, url: url, pdf: pdf, stackId: selected == "new" ? "" : selected, newStackName: selected == "new" ? newStackName.trimmingCharacters(in: .whitespacesAndNewlines) : nil)
            saved = true
            ShareQueue.shared.reload()
            Task { await ShareQueue.shared.sync() }
        } catch { self.error = error.localizedDescription }
    }
    func close() {
        if saved { context?.completeRequest(returningItems: nil) }
        else { context?.cancelRequest(withError: NSError(domain: NSCocoaErrorDomain, code: NSUserCancelledError)) }
    }
}

struct ShareView: View {
    @ObservedObject var model: ShareModel
    @ObservedObject var queue = ShareQueue.shared
    var body: some View {
        NavigationStack {
            Form {
                if model.saved {
                    Section("Shared article") {
                        Label("Added", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                        Text(model.title).font(.headline)
                        Text("Open Reading Stacks to view this shared article.").font(.footnote)
                        Button("Done", action: model.close)
                    }
                } else {
                    Section("Article or PDF") { Text(model.title).font(.footnote) }
                    Picker("Destination", selection: $model.selected) {
                        Text("Create new stack").tag("new")
                        ForEach(queue.state.destinations) { Text($0.name).tag($0.id) }
                    }.pickerStyle(.menu)
                    if model.selected == "new" {
                        TextField("New stack name", text: $model.newStackName)
                    }
                    Button("Add to stack", action: model.save)
                        .disabled(model.loading || (model.url.isEmpty && model.pdf == nil) || (model.selected == "new" && !(1...200).contains(model.newStackName.trimmingCharacters(in: .whitespacesAndNewlines).count)))
                }
                if model.loading { ProgressView("Reading share…") }
                if !model.error.isEmpty { Text(model.error).foregroundStyle(.red) }
            }.navigationTitle("Add to stack")
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button(model.saved ? "Done" : "Cancel", action: model.close) } }
        }
    }
}

final class ShareViewController: UIViewController {
    private let model = ShareModel()
    override func viewDidLoad() {
        super.viewDidLoad()
        let host = UIHostingController(rootView: ShareView(model: model))
        addChild(host); view.addSubview(host.view)
        host.view.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([host.view.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            host.view.trailingAnchor.constraint(equalTo: view.trailingAnchor), host.view.topAnchor.constraint(equalTo: view.topAnchor),
            host.view.bottomAnchor.constraint(equalTo: view.bottomAnchor)])
        host.didMove(toParent: self); model.start(extensionContext)
    }
}
