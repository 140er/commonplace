import Foundation
import WebKit

struct HubError: LocalizedError {
    let status: Int
    var errorDescription: String? { status == 401 ? "Signed out" : "Request failed (\(status))" }
}

@MainActor
final class Hub: ObservableObject {
    enum State { case checking, signedOut, signedIn }
    static let base = URL(string: "https://app.cmmn.dev")!
    @Published var state: State = .checking

    private let session: URLSession = {
        let c = URLSessionConfiguration.default
        c.httpCookieStorage = HTTPCookieStorage.shared
        return URLSession(configuration: c)
    }()

    func restore() async {
        await syncCookies()
        await refresh()
    }

    func refresh() async {
        do { _ = try await request("/api/me"); state = .signedIn }
        catch { state = .signedOut }
    }

    func syncCookies() async {
        let cookies = await WKWebsiteDataStore.default().httpCookieStore.allCookies()
        for cookie in cookies where cookie.domain.hasSuffix("cmmn.dev") {
            HTTPCookieStorage.shared.setCookie(cookie)
        }
    }

    func signOut() async {
        let store = WKWebsiteDataStore.default()
        let records = await store.dataRecords(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes())
        await store.removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), for: records)
        HTTPCookieStorage.shared.cookies(for: Hub.base)?.forEach { HTTPCookieStorage.shared.deleteCookie($0) }
        state = .signedOut
    }

    @discardableResult
    func request(_ path: String, method: String = "GET", body: [String: Any]? = nil) async throws -> Any {
        var req = URLRequest(url: URL(string: path, relativeTo: Hub.base)!)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await session.data(for: req)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            if status == 401 { state = .signedOut }
            throw HubError(status: status)
        }
        return data.isEmpty ? [:] : ((try? JSONSerialization.jsonObject(with: data)) ?? [:])
    }
}
