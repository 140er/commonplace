import SwiftUI

struct ChatMessage: Identifiable {
    let id: Int
    let author: String
    let text: String
}

@MainActor
final class ChatModel: ObservableObject {
    @Published var projects: [(slug: String, name: String)] = []
    @Published var slug: String?
    @Published var messages: [ChatMessage] = []
    @Published var error: String?

    func start(_ hub: Hub) async {
        do {
            let json = try await hub.request("/api/chat") as? [String: Any] ?? [:]
            projects = (json["projects"] as? [[String: Any]] ?? []).compactMap {
                guard let s = $0["slug"] as? String else { return nil }
                return (s, $0["name"] as? String ?? s)
            }
            slug = slug ?? json["default"] as? String ?? projects.first?.slug
            await load(hub)
        } catch { self.error = error.localizedDescription }
    }

    func load(_ hub: Hub) async {
        guard let slug else { return }
        do {
            let json = try await hub.request("/api/chat/\(slug)?last=50") as? [String: Any] ?? [:]
            messages = (json["messages"] as? [[String: Any]] ?? []).compactMap { m in
                guard let id = m["id"] as? Int else { return nil }
                let who = (m["author"] ?? m["actor"] ?? m["sender"] ?? m["from"]) as? String ?? ""
                return ChatMessage(id: id, author: who.replacingOccurrences(of: "conn:", with: "").replacingOccurrences(of: "user:", with: ""),
                                   text: m["text"] as? String ?? "")
            }
            error = nil
        } catch { self.error = error.localizedDescription }
    }

    func send(_ text: String, hub: Hub) async {
        guard let slug else { return }
        do {
            try await hub.request("/api/boards/\(slug)/chat", method: "POST", body: ["text": text])
            await load(hub)
        } catch { self.error = error.localizedDescription }
    }
}

struct ChatView: View {
    @ObservedObject var hub: Hub
    @StateObject private var model = ChatModel()
    @State private var draft = ""

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 12) {
                            if let error = model.error { Text(error).foregroundStyle(.red) }
                            ForEach(model.messages) { m in
                                VStack(alignment: .leading, spacing: 2) {
                                    if !m.author.isEmpty { Text(m.author).font(.caption).foregroundStyle(.secondary) }
                                    Text(m.text)
                                }.id(m.id)
                            }
                        }.padding()
                    }
                    .onChange(of: model.messages.count) { _, _ in
                        if let last = model.messages.last { proxy.scrollTo(last.id, anchor: .bottom) }
                    }
                }
                HStack {
                    TextField("Message", text: $draft, axis: .vertical).textFieldStyle(.roundedBorder).lineLimit(1...4)
                    Button("Send") {
                        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !text.isEmpty else { return }
                        draft = ""
                        Task { await model.send(text, hub: hub) }
                    }.disabled(draft.trimmingCharacters(in: .whitespaces).isEmpty)
                }.padding()
            }
            .navigationTitle(model.projects.first { $0.slug == model.slug }?.name ?? "Chat")
            .toolbar {
                if model.projects.count > 1 {
                    Menu("Project") {
                        ForEach(model.projects, id: \.slug) { p in
                            Button(p.name) { model.slug = p.slug; Task { await model.load(hub) } }
                        }
                    }
                }
            }
            .refreshable { await model.load(hub) }
            .task { await model.start(hub) }
        }
    }
}
