from __future__ import annotations

import html as html_module
import json
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

from .common import base_model, extract_model


class _StructuredDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.capture = False
        self.buffer: list[str] = []
        self.documents: list[object] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag.lower() == "script" and values.get("type", "").lower() == "application/ld+json":
            self.capture = True
            self.buffer = []

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self.capture:
            self.capture = False
            try:
                self.documents.append(json.loads("".join(self.buffer)))
            except json.JSONDecodeError:
                pass


def product_items(page: str) -> list[dict]:
    parser = _StructuredDataParser()
    parser.feed(page)
    products: list[dict] = []

    def visit(node: object) -> None:
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            if node.get("@type") == "ListItem" and isinstance(node.get("item"), dict):
                item = node["item"]
                if item.get("@type") == "Product":
                    products.append(item)
            for key in ("@graph", "itemListElement"):
                if key in node:
                    visit(node[key])
    for document in parser.documents:
        visit(document)
    unique: dict[str, dict] = {}
    for item in products:
        url = str(item.get("url") or item.get("@id") or "")
        if url:
            unique.setdefault(url, item)
    return list(unique.values())


def support_urls(sitemap: str) -> list[str]:
    return sorted(set(html_module.unescape(value) for value in re.findall(r"<loc>\s*(.*?)\s*</loc>", sitemap, re.I)))


def page_title(page: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
    return re.sub(r"\s+", " ", html_module.unescape(match.group(1))).strip() if match else None


def parse_dimensions(name: str, url: str) -> dict[str, str | None]:
    text = f"{name} {url}".lower()
    size_match = re.search(r"(?:\b(\d{2})\s*mm\b|\b(\d[.,]\d)\s*cm\b)", text)
    size = None
    if size_match:
        size = f"{size_match.group(1)} mm" if size_match.group(1) else f"{size_match.group(2).replace(',', '.')} cm"
    if re.search(r"\b(lte|4g)\b", text):
        connectivity = "LTE"
    elif re.search(r"\b(bluetooth|wi-fi|wifi)\b", text):
        connectivity = "Bluetooth/Wi-Fi"
    else:
        connectivity = None
    colours = ("graphite", "silver", "cream", "green", "black", "white", "gray", "grey", "blue", "orange")
    colour = next((value.title() for value in colours if re.search(rf"\b{value}\b", text)), None)
    return {"size": size, "connectivity": connectivity, "colour": colour}


def family_name(name: str) -> str | None:
    match = re.search(r"(Galaxy\s+Watch\s*(?:Ultra\s*\d*|\d+\s*Classic|\d+|Classic|[A-Z][A-Za-z]+))", name, re.I)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else None


def classify_product(name: str, url: str) -> tuple[str, tuple[str, ...]]:
    text = f"{name} {url}".lower()
    evidence = ["official_samsung_watches_category"]
    if "/watches/" not in urlparse(url).path.lower():
        return "non_smartwatch", tuple(evidence + ["outside_watches_url_namespace"])
    if any(term in text for term in ("strap", "band", "charger", "accessor")):
        return "non_smartwatch", tuple(evidence + ["accessory_term"])
    if "galaxy fit" in text:
        return "non_smartwatch", tuple(evidence + ["fitness_band_family"])
    if "galaxy ring" in text:
        return "non_smartwatch", tuple(evidence + ["smart_ring_family"])
    if "galaxy watch" in text:
        return "known_smartwatch", tuple(evidence + ["explicit_galaxy_watch_name"])
    return "probable_smartwatch", tuple(evidence + ["unknown_family_in_watch_category"])


def normalized_product(item: dict, region: str) -> dict:
    url = str(item.get("url") or item.get("@id") or "")
    name = str(item.get("name") or "").strip()
    model = extract_model(url)
    dimensions = parse_dimensions(name, urlparse(url).path.replace("-", " "))
    classification, evidence = classify_product(name, url)
    offers = item.get("offers") if isinstance(item.get("offers"), dict) else {}
    family = family_name(name)
    stable_raw = {key: value for key, value in item.items() if key not in {"aggregateRating", "review"}}
    return {
        "url": url, "name": name, "model": model, "base_model": base_model(model),
        "family": family, "family_identity": family.lower().replace(" ", "-") if family else None,
        "region": region, "classification": classification, "evidence": evidence,
        "size": dimensions["size"], "connectivity": dimensions["connectivity"], "colour": dimensions["colour"],
        "price": str(offers.get("price")) if offers.get("price") is not None else None,
        "currency": offers.get("priceCurrency"), "availability": offers.get("availability"), "raw": stable_raw,
    }
