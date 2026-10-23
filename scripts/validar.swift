// Comprueba con WebKit (el mismo motor que el navegador) que una lista de reglas compila.
// Uso: swift scripts/validar.swift auto-rules.json   → sale con código 1 si no compila.
import WebKit
let path = CommandLine.arguments[1]
let json = try! String(contentsOfFile: path, encoding: .utf8)
let store = WKContentRuleListStore(url: URL(fileURLWithPath: NSTemporaryDirectory() + "pubo-validar"))!
store.compileContentRuleList(forIdentifier: "validar", encodedContentRuleList: json) { list, error in
    if list != nil { print("✅ \(path): WebKit la acepta"); exit(0) }
    print("❌ \(path) no compila: \(error?.localizedDescription ?? "?")"); exit(1)
}
RunLoop.main.run()
