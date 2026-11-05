#!/usr/bin/env python3
"""Convierte EasyList Spanish al formato de reglas de WebKit (WKContentRuleList) para la lista
«Automáticas» de pUbo. Cada ejecución REHACE la lista entera (las reglas viejas se van y entran las
nuevas). Solo se convierte lo que WebKit entiende sin riesgo:
  · ocultar elementos en webs concretas:   dominio.es,otra.com##selector
  · bloquear dominios:                     ||dominio.com^   (y $third-party)
  · bloquear rutas y recursos:             ||dominio.com/ads/, $script, $domain=web.es…
  · excepciones de dominio:                @@||dominio.com^ (al final, ignore-previous-rules)
Se descarta lo genérico (##selector sin web, para no repetir lo de Gmail), los selectores
especiales de ABP/uBlock (:has-text, :xpath, +js…) y las reglas con opciones que no se pueden
traducir con seguridad."""
import json, re, sys, urllib.request

SOURCE = "https://easylist-downloads.adblockplus.org/easylistspanish.txt"
MAX_RULES = 5000
DOMAIN_PREFIX = "^[^:]+://+([^:/]+\\.)?"
BAD_SELECTOR = re.compile(r":-abp-|:has-text|:contains|:xpath|:upward|:remove|:style|:matches-|:min-text|:watch-attr|:others|\+js\(|\[-ext-")
DOMAIN_RE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")

def domain_filter(host):
    return DOMAIN_PREFIX + re.escape(host).replace("\\-", "-") + "[:/]"

# Tipos de recurso de ABP → WebKit. Los que no están (popup, websocket, webrtc…) no se convierten.
TYPES = {"script": ["script"], "image": ["image"], "stylesheet": ["style-sheet"], "font": ["font"],
         "media": ["media"], "subdocument": ["document"], "xmlhttprequest": ["raw"], "ping": ["ping"],
         "object": ["raw"], "other": ["raw"]}
SEP = "[^a-zA-Z0-9_.%-]"          # el «^» de ABP: cualquier separador (/, ?, :, &…)

def network_trigger(body):
    """||dominio/ruta$opciones → trigger de WebKit, o None si no se puede traducir con seguridad."""
    pattern, _, opts = body.partition("$")
    m = re.match(r"^\|\|([a-z0-9.-]+)(.*)$", pattern)
    if not m or not DOMAIN_RE.match(m.group(1).lower()):
        return None
    host, rest = m.group(1).lower(), m.group(2)
    regex = DOMAIN_PREFIX + re.escape(host).replace("\\-", "-")
    for i, ch in enumerate(rest):
        if ch == "*":
            regex += ".*"
        elif ch == "^":
            regex += SEP if i == 0 or i < len(rest) - 1 else ""   # «^» final tras una ruta: da igual lo que siga
        elif ch == "|":
            if i != len(rest) - 1:
                return None
            regex += "$"
        elif ch in ".?+()[]{}\\$":
            regex += "\\" + ch
        else:
            regex += ch
    if not rest:
        regex += SEP
    trigger = {"url-filter": regex}
    types = []
    for opt in filter(None, opts.split(",")):
        opt = opt.strip().lower()
        if opt == "third-party":
            trigger["load-type"] = ["third-party"]
        elif opt in ("~third-party", "first-party", "1p"):
            trigger["load-type"] = ["first-party"]
        elif opt == "important":
            pass
        elif opt in TYPES:
            types += TYPES[opt]
        elif opt.startswith("domain="):
            webs = opt[7:].split("|")
            si = [w for w in webs if not w.startswith("~")]
            no = [w[1:] for w in webs if w.startswith("~")]
            if (si and no) or not all(DOMAIN_RE.match(w) for w in si + no):
                return None                     # mezcla de «sí» y «no»: WebKit no lo permite junto
            if si:
                trigger["if-domain"] = ["*" + w for w in si]
            else:
                trigger["unless-domain"] = ["*" + w for w in no]
        else:
            return None                         # popup, ~script, redirect, csp…: no se traduce
    if types:
        trigger["resource-type"] = sorted(set(types))
    return trigger

def convert(text):
    cosmetic = {}          # tupla de webs -> [selectores]
    block, block3p, allow = set(), set(), set()
    network, network_allow = [], []      # reglas de red más finas (rutas, tipos, webs)
    version = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("! Version:"):
            version = line.split(":", 1)[1].strip()
        if not line or line.startswith("!") or line.startswith("["):
            continue
        if "#@#" in line or "#?#" in line or "#$#" in line or "#%#" in line:
            continue
        if "##" in line:
            sites, selector = line.split("##", 1)
            if not sites or "~" in sites or BAD_SELECTOR.search(selector) or "{" in selector:
                continue
            hosts = tuple(sorted(h.strip().lower() for h in sites.split(",") if DOMAIN_RE.match(h.strip().lower())))
            if hosts and selector.strip():
                cosmetic.setdefault(hosts, []).append(selector.strip())
            continue
        exception = line.startswith("@@")
        body = line[2:] if exception else line
        m = re.match(r"^\|\|([a-z0-9.-]+)\^(\$(.*))?$", body.lower())
        simple = m and DOMAIN_RE.match(m.group(1)) and (not m.group(3) or (m.group(3) == "third-party" and not exception))
        if not simple:
            if body.startswith("||"):
                trigger = network_trigger(body)
                if trigger:
                    (network_allow if exception else network).append(trigger)
            continue
        opts = m.group(3) or ""
        host = m.group(1)
        if exception:
            if not opts:
                allow.add(host)
        elif not opts:
            block.add(host)
        elif opts == "third-party":
            block3p.add(host)

    rules = []
    for host in sorted(block):
        rules.append({"trigger": {"url-filter": domain_filter(host)}, "action": {"type": "block"}})
    for host in sorted(block3p - block):
        rules.append({"trigger": {"url-filter": domain_filter(host), "load-type": ["third-party"]}, "action": {"type": "block"}})
    seen = set()
    for trigger in network:
        key = json.dumps(trigger, sort_keys=True)
        if key not in seen:
            seen.add(key)
            rules.append({"trigger": trigger, "action": {"type": "block"}})
    for hosts, selectors in sorted(cosmetic.items()):
        unique = sorted(set(selectors))
        for i in range(0, len(unique), 40):      # trozos para no hacer selectores enormes
            rules.append({"trigger": {"url-filter": ".*", "if-domain": ["*" + h for h in hosts]},
                          "action": {"type": "css-display-none", "selector": ", ".join(unique[i:i + 40])}})
    rules = rules[:MAX_RULES - len(allow) - len(network_allow)]
    for host in sorted(allow):                    # excepciones: siempre al final
        rules.append({"trigger": {"url-filter": domain_filter(host)}, "action": {"type": "ignore-previous-rules"}})
    for trigger in network_allow:
        rules.append({"trigger": trigger, "action": {"type": "ignore-previous-rules"}})
    return rules, version, {"bloquear": len(block), "terceros": len(block3p - block),
                            "rutas_y_recursos": len(seen), "ocultar_webs": len(cosmetic),
                            "excepciones": len(allow) + len(network_allow)}

if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else None
    text = open(src, encoding="utf-8").read() if src else urllib.request.urlopen(SOURCE, timeout=60).read().decode("utf-8")
    rules, version, stats = convert(text)
    with open("auto-rules.json", "w", encoding="utf-8") as f:
        f.write("[\n" + ",\n".join("  " + json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rules) + "\n]\n")
    with open("auto-origen.json", "w", encoding="utf-8") as f:
        json.dump({"origen": "EasyList Spanish", "url": SOURCE, "version_origen": version,
                   "reglas": len(rules), **stats}, f, ensure_ascii=False, indent=1)
    print(f"auto-rules.json: {len(rules)} reglas · EasyList Spanish {version} · {stats}")
