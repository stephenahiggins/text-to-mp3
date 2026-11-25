import UIKit
import SwiftUI
import React
import Combine

@objc(ReadingShareQueue)
final class ReadingShareQueue: RCTEventEmitter {
    private var queueChanges: AnyCancellable?
    @objc override static func requiresMainQueueSetup() -> Bool { true }
    override func supportedEvents() -> [String]! { ["savedSharesChanged"] }
    override func startObserving() {
        Task { @MainActor in
            let queue = ShareQueue.shared
            queue.reload()
            // @Published immediately emits the current count, then subsequent edits.
            queueChanges = queue.$state.map { $0.items.count }.removeDuplicates().sink { [weak self] count in
                self?.sendEvent(withName: "savedSharesChanged", body: ["count": count])
            }
        }
    }
    override func stopObserving() {
        Task { @MainActor in queueChanges = nil }
    }
    @objc func setActive(_ active: Bool) {
        Task { @MainActor in
            let queue = ShareQueue.shared
            queue.setActive(active)
            if active, let id = queue.state.pendingPresentation,
               queue.state.items.contains(where: { $0.id == id }) {
                showSharedArticle(id)
            }
        }
    }
    @objc func present() {
        Task { @MainActor in showSharedArticle(nil) }
    }
    @MainActor private func showSharedArticle(_ id: String?) {
        guard let scene = UIApplication.shared.connectedScenes.first(where: { $0.activationState == .foregroundActive }) as? UIWindowScene,
              var controller = scene.windows.first(where: { $0.isKeyWindow })?.rootViewController else { return }
        while let presented = controller.presentedViewController { controller = presented }
        if let existing = controller as? UIHostingController<ShareQueueView> {
            existing.rootView = ShareQueueView(focusedItemID: id)
        } else {
            controller.present(UIHostingController(rootView: ShareQueueView(focusedItemID: id)), animated: true)
        }
    }
}
