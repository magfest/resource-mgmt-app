"""Display words for coded TechOps line fields.

The request pages and the worklist CSV both read these maps, so the screen
and a downloaded spreadsheet cannot describe the same answer two ways.
Registered as the `techops_labels` Jinja global in app/__init__.py.
"""

PHONE_PURPOSE = {"VOICE": "Voice", "TEXT": "Text", "BOTH": "Voice & text"}

PRINTER_CONNECTIVITY = {"ETHERNET": "Ethernet", "USB": "USB"}

PRINTER_VOLUME = {
    "UNDER_50": "under 50 pages/day",
    "50_250": "50 to 250 pages/day",
    "250_500": "250 to 500 pages/day",
    "OVER_500": "more than 500 pages/day",
}

NETWORK_KIND = {
    "COMPUTERS": "Laptops or desktops",
    "STREAMING": "Streaming or capture gear",
    "SCANNERS": "Badge scanners",
    "PRINTERS": "Printers",
    "CONSOLES": "Consoles or arcade hardware",
    "OTHER": "Something else",
}

NETWORK_TRAFFIC = {
    "INTERNET": "Mostly internet",
    "LOCAL": "Mostly local network",
    "UNSURE": "Not sure",
}
