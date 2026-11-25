import Foundation

// Compiled against the actual iOS source and run as a simulator CLI executable.
@main struct QueueStorageTests {
    static func main() throws {
        let root = try QueueStore.root()
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        if CommandLine.arguments.last == "append" {
            for n in 0..<25 { try QueueStore.enqueue(title: "Item \(n)", url: "https://example.com/\(n)", pdf: nil, stackId: "") }
            return
        }
        if CommandLine.arguments.last == "verify" {
            let state = try QueueStore.transaction { $0 }
            precondition(state.items.count == 101, "Concurrent writers lost a share")
            precondition(Set(state.items.map(\.id)).count == 101)
            precondition(state.items.contains { $0.id == state.pendingPresentation }, "Latest share handoff was lost")
            print("PASS: concurrent processes preserved all shares across restart")
            return
        }
        if CommandLine.arguments.count > 3 {
            let plist = try PropertyListSerialization.propertyList(from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[2])), format: nil) as! [String: Any]
            let ext = plist["NSExtension"] as! [String: Any]
            let attributes = ext["NSExtensionAttributes"] as! [String: Any]
            let predicate = NSPredicate(format: attributes["NSExtensionActivationRule"] as! String)
            func accepts(_ types: [String]) -> Bool {
                predicate.evaluate(with: ["extensionItems": [["attachments": types.map { ["registeredTypeIdentifiers": [$0]] }]]])
            }
            precondition(accepts(["public.url"]))
            precondition(accepts(["com.adobe.pdf"]))
            precondition(accepts(["com.adobe.pdf", "public.url"]))
            precondition(!accepts(["public.jpeg"]))
            precondition(!accepts(["com.adobe.pdf", "com.adobe.pdf"]))
            print("PASS: activation accepts URLs and PDF plus source URL, rejects images and multiple PDFs")
        }
        let legacy = Data(#"{"items":[],"stacks":[],"lastStack":""}"#.utf8)
        let oldState = try JSONDecoder().decode(QueueState.self, from: legacy)
        precondition(oldState.pendingPresentation == nil)
        let pdf = Data("%PDF-test payload".utf8)
        try QueueStore.enqueue(title: "Saved PDF", url: "", pdf: pdf, stackId: "stack")
        let first = try QueueStore.transaction { $0 }
        precondition(first.items.count == 1 && first.lastStack == "stack")
        precondition(first.pendingPresentation == first.items[0].id)
        let file = root.appendingPathComponent(first.items[0].file!)
        let copied = try Data(contentsOf: file)
        precondition(copied == pdf, "PDF wasn't copied durably")
        do {
            try QueueStore.enqueue(title: "Oversized", url: "", pdf: Data(count: 20_000_001), stackId: "")
            fatalError("Accepted oversized file")
        } catch is ShareFailure {}
        let after = try QueueStore.transaction { $0 }
        precondition(after.items.count == 1, "Failed save changed the queue")
        precondition(after.pendingPresentation == first.pendingPresentation, "Failed save changed the handoff")
        let creatorID = try QueueStore.enqueue(title: "First", url: "https://example.com/first", pdf: nil, stackId: "", newStackName: "Weekend reading")
        let draft = try QueueStore.transaction { $0 }
        let stackID = "shared-" + creatorID
        precondition(draft.suggestedDestination == stackID)
        precondition(draft.destinations.first { $0.id == stackID }?.name == "Weekend reading")
        let followUpID = try QueueStore.enqueue(title: "Follow-up", url: "https://example.com/next", pdf: nil, stackId: stackID)
        try QueueStore.transaction { state in
            let followUp = state.items.first { $0.id == followUpID }!
            precondition(followUp.destinationID == stackID && followUp.newStackName == "Weekend reading")
            precondition(state.items.filter { $0.destinationID == stackID }.count == 2)
            try state.renameDraft(stackID, to: "  Sunday reading  ")
        }
        let renamed = try QueueStore.transaction { $0 }
        let renamedShares = renamed.items.filter { $0.destinationID == stackID }
        precondition(renamedShares.map(\.id) == [creatorID, followUpID])
        precondition(renamedShares.allSatisfy { $0.newStackName == "Sunday reading" })
        precondition(renamed.suggestedDestination == stackID)
        let newerID = try QueueStore.enqueue(title: "Third", url: "https://example.com/third", pdf: nil, stackId: stackID)
        let newer = try QueueStore.transaction { $0 }
        precondition(newer.items.first { $0.id == newerID }?.newStackName == "Sunday reading")
        try QueueStore.transaction { state in
            let creator = state.items.firstIndex { $0.id == creatorID }!
            state.items[creator].status = "processing"
            do {
                try state.renameDraft(stackID, to: "Should fail")
                fatalError("Renamed a stack with an in-flight share")
            } catch is ShareFailure {}
            precondition(state.items[creator].newStackName == "Sunday reading")
            state.items[creator].status = "error"
            state.pendingPresentation = creatorID
            try state.renameDraft(stackID, to: "Next weekend")
            precondition(state.items[creator].id != creatorID && state.items[creator].destinationID == stackID)
            precondition(state.pendingPresentation == state.items[creator].id)
            precondition(state.items[creator].status == "error")
            state.items.remove(at: creator)
            precondition(state.destinations.first { $0.id == stackID }?.name == "Next weekend")
            state.stacks.append(Stack(id: stackID, name: "On Mac"))
            precondition(!state.canRenameDraft(stackID))
            state.items.removeAll { $0.destinationID == stackID }
            state.stacks.removeAll { $0.id == stackID }
            state.pendingPresentation = first.pendingPresentation
        }
        print("PASS: offline stack rename persists across shares and protects submitted destinations")
        let lock = try QueueStore.lock("sync.lock", nonblocking: true)
        do { _ = try QueueStore.lock("sync.lock", nonblocking: true); fatalError("Concurrent sync allowed") }
        catch is ShareFailure {}
        Darwin.close(lock)
        print("PASS: durable PDF copy, limits, atomic save and exclusive sync")
    }
}
