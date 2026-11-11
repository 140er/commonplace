import SwiftUI

@main
struct CommonplaceApp: App {
    @StateObject private var hub = Hub()
    var body: some Scene {
        WindowGroup {
            Group {
                switch hub.state {
                case .checking: ProgressView()
                case .signedOut: SignInView(hub: hub)
                case .signedIn: RootView(hub: hub)
                }
            }
            .task { await hub.restore() }
        }
    }
}

struct RootView: View {
    @ObservedObject var hub: Hub
    var body: some View {
        TabView {
            NeedsMeView(hub: hub).tabItem { Label("Needs me", systemImage: "tray.full") }
            ChatView(hub: hub).tabItem { Label("Chat", systemImage: "bubble.left.and.bubble.right") }
        }
    }
}
