import SwiftUI
import WebKit

struct SignInView: View {
    @ObservedObject var hub: Hub
    @StateObject private var model = WebModel()

    var body: some View {
        NavigationStack {
            WebView(model: model, onNavigate: { await hub.syncCookies(); await hub.refresh() })
                .navigationTitle("Sign in")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    Button("Paste link") {
                        if let text = UIPasteboard.general.string, let url = URL(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
                           url.host?.hasSuffix("cmmn.dev") == true {
                            model.view.load(URLRequest(url: url))
                        }
                    }
                }
        }
    }
}

@MainActor
final class WebModel: ObservableObject {
    let view: WKWebView = {
        let v = WKWebView(frame: .zero, configuration: WKWebViewConfiguration())
        v.load(URLRequest(url: URL(string: "https://app.cmmn.dev/app/")!))
        return v
    }()
}

struct WebView: UIViewRepresentable {
    let model: WebModel
    let onNavigate: () async -> Void
    func makeCoordinator() -> Coordinator { Coordinator(onNavigate) }
    func makeUIView(context: Context) -> WKWebView {
        model.view.navigationDelegate = context.coordinator
        return model.view
    }
    func updateUIView(_ view: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate {
        let onNavigate: () async -> Void
        init(_ f: @escaping () async -> Void) { onNavigate = f }
        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            Task { await onNavigate() }
        }
    }
}
