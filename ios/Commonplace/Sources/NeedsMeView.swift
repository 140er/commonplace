import SwiftUI

struct NeedItem: Identifiable {
    let number: Int
    let title: String
    let version: Int?
    let group: String
    let options: [String]
    let canApprove: Bool
    var id: String { "\(group)-\(number)" }
}

@MainActor
final class NeedsMeModel: ObservableObject {
    @Published var items: [NeedItem] = []
    @Published var error: String?
    @Published var busy: Set<String> = []

    func load(_ hub: Hub) async {
        do {
            let json = try await hub.request("/api/needs-me") as? [String: Any] ?? [:]
            var out: [NeedItem] = []
            for (group, value) in json.sorted(by: { $0.key < $1.key }) {
                guard let rows = value as? [[String: Any]] else { continue }
                for row in rows {
                    guard let number = row["number"] as? Int ?? (row["card"] as? Int) else { continue }
                    let actions = row["actions"] as? [String] ?? []
                    out.append(NeedItem(
                        number: number,
                        title: row["title"] as? String ?? "Card \(number)",
                        version: row["version"] as? Int,
                        group: group,
                        options: row["ask_options"] as? [String] ?? [],
                        canApprove: actions.contains("approve") || group == "plans" || group == "ready_to_merge"))
                }
            }
            items = out; error = nil
        } catch { self.error = error.localizedDescription }
    }

    func approve(_ item: NeedItem, hub: Hub) async {
        await act(item, hub, "/api/cards/\(item.number)/approve", ["approve": true, "version": item.version as Any])
    }
    func choose(_ item: NeedItem, _ choice: String, hub: Hub) async {
        await act(item, hub, "/api/cards/\(item.number)/decide", ["choice": choice, "version": item.version as Any])
    }
    private func act(_ item: NeedItem, _ hub: Hub, _ path: String, _ body: [String: Any]) async {
        busy.insert(item.id); defer { busy.remove(item.id) }
        do {
            try await hub.request(path, method: "POST", body: body.compactMapValues { $0 is NSNull ? nil : $0 })
            await load(hub)
        } catch { self.error = error.localizedDescription }
    }
}

struct NeedsMeView: View {
    @ObservedObject var hub: Hub
    @StateObject private var model = NeedsMeModel()

    var body: some View {
        NavigationStack {
            List {
                if let error = model.error { Text(error).foregroundStyle(.red) }
                ForEach(model.items) { item in
                    VStack(alignment: .leading, spacing: 8) {
                        Text("#\(item.number) \(item.title)")
                        HStack {
                            ForEach(item.options, id: \.self) { option in
                                Button(option) { Task { await model.choose(item, option, hub: hub) } }
                                    .buttonStyle(.bordered)
                            }
                            if item.options.isEmpty && item.canApprove {
                                Button("Approve") { Task { await model.approve(item, hub: hub) } }
                                    .buttonStyle(.borderedProminent)
                            }
                        }
                        .disabled(model.busy.contains(item.id))
                    }
                    .padding(.vertical, 4)
                }
            }
            .overlay { if model.items.isEmpty && model.error == nil { Text("Nothing needs you").foregroundStyle(.secondary) } }
            .navigationTitle("Needs me")
            .toolbar { Button("Sign out") { Task { await hub.signOut() } } }
            .refreshable { await model.load(hub) }
            .task { await model.load(hub) }
        }
    }
}
