import time
import random
import re
import json
import os
from typing import Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def format_duration(seconds: float) -> str:
    """Formats seconds into human-readable duration, e.g. '2 phút 15 giây' or '45 giây'."""
    sec = int(round(seconds))
    if sec <= 0:
        return "0 giây"
    if sec < 60:
        return f"{sec} giây"
    mins = sec // 60
    rem_sec = sec % 60
    if mins < 60:
        return f"{mins} phút {rem_sec} giây" if rem_sec else f"{mins} phút"
    hrs = mins // 60
    rem_mins = mins % 60
    return f"{hrs} giờ {rem_mins} phút" + (f" {rem_sec} giây" if rem_sec else "")


def print_summary_table(results, duration_str: Optional[str] = None):
    print("\n")
    print("=" * 55)
    print(f"{'🚀 FINAL POSTING SUMMARY':^55}")
    print("=" * 55)

    # Updated headers to reflect the Group ID
    print(f"{'GROUP ID':<30} | {'STATUS':<20}")
    print("-" * 55)

    success_count = 0
    for row in results:
        # Extract the Group ID using Regex
        # This looks for '/groups/' and grabs all the numbers immediately after it
        match = re.search(r'/groups/([a-zA-Z0-9_.]+)', row['link'])

        # If it finds a match, use the numbers. If not, print an error label.
        group_id = match.group(1) if match else "Invalid Link Format"

        print(f"{group_id:<30} | {row['status']:<20}")

        if "SUCCESS" in row['status'].upper():
            success_count += 1

    print("=" * 55)
    print(f"📊 Total Successful Posts: {success_count} / {len(results)}")
    if duration_str:
        print(f"⏱ Completion Time: {duration_str}")
    print("=" * 55)
    print("\n")


def print_join_summary_table(results: list, duration_str: Optional[str] = None):
    """
    Prints a clear, formatted summary table of group join results.
    Each item in results is a dict: {'link': url, 'status': status, 'details': message, ...}
    """
    print("\n")
    print("=" * 70)
    print(f"{'🚀 FINAL GROUP JOINING SUMMARY':^70}")
    print("=" * 70)
    print(f"{'GROUP ID':<30} | {'STATUS':<20} | {'DETAILS'}")
    print("-" * 70)

    already_joined = 0
    join_sent = 0
    instant_joined = 0
    failed_or_blocked = 0

    for row in results:
        link = row.get("link", "")
        status = row.get("status", "UNKNOWN")
        details = row.get("details", "")

        match = re.search(r'/groups/([a-zA-Z0-9_.]+)', link)
        group_id = match.group(1) if match else (link if len(link) <= 28 else link[:25] + "...")

        print(f"{group_id:<30} | {status:<20} | {details}")

        st_upper = status.upper()
        if "ALREADY_JOINED" in st_upper:
            already_joined += 1
        elif "JOINED_SUCCESS" in st_upper:
            instant_joined += 1
        elif "JOIN_REQUEST_SENT" in st_upper:
            join_sent += 1
        elif "DRY_RUN" in st_upper:
            pass
        else:
            failed_or_blocked += 1

    print("=" * 70)
    print(f"📊 Total Groups Checked: {len(results)}")
    print(f"   - Đã là thành viên từ trước      : {already_joined}")
    print(f"   - Gia nhập thành công (Vào ngay) : {instant_joined}")
    print(f"   - Đã gửi yêu cầu (Chờ duyệt)     : {join_sent}")
    if failed_or_blocked > 0:
        print(f"   - Bị hạn chế / Thất bại          : {failed_or_blocked}")
    if duration_str:
        print(f"⏱ Completion Time: {duration_str}")
    print("=" * 70)
    print("\n")


def human_delay(min_sec=1, max_sec=3):
    time.sleep(random.uniform(min_sec, max_sec))


def extract_facebook_group_urls(text: str) -> list:
    """
    Extracts clean, unique, normalized Facebook group URLs from any raw text.
    Canonicalizes to https://www.facebook.com/groups/<group_id> to deduplicate
    fb.com vs facebook.com, query parameters, trailing slashes, and case.
    """
    if not text or not isinstance(text, str):
        return []
    pattern = r"https?://(?:www\.)?(?:facebook\.com|fb\.com)/groups/([a-zA-Z0-9._\-]+)"
    matches = re.finditer(pattern, text, flags=re.IGNORECASE)
    seen = set()
    result = []
    for m in matches:
        group_id = m.group(1)
        canonical = f"https://www.facebook.com/groups/{group_id}"
        if group_id.lower() not in seen:
            seen.add(group_id.lower())
            result.append(canonical)
    return result


def parse_group_header_info(raw_title: str, raw_subtitle: str = "", membership: Optional[str] = None) -> dict:
    """Parses Facebook group header into name, member count, privacy, and membership status."""
    name = re.sub(r"^\(\d+\+?\)\s*", "", raw_title.strip())
    name = re.sub(r"\s*\|\s*Facebook\s*$", "", name, flags=re.IGNORECASE).strip()
    if name.lower() in ("facebook", ""):
        name = "—"

    sub = raw_subtitle.lower()
    privacy = "Công khai" if ("công khai" in sub or "public" in sub) else (
        "Riêng tư" if ("riêng tư" in sub or "private" in sub) else "Chưa rõ"
    )
    mem_match = re.search(r"([\d.,]+\s*[kKmMbB]?)\s*(?:thành viên|members?|người tham gia)", raw_subtitle, re.IGNORECASE)
    members = mem_match.group(1).replace(" ", "") if mem_match else "—"
    res = {"name": name or "—", "members": members, "privacy": privacy}
    if membership is not None:
        res["membership"] = membership
    return res


def load_group_targets(filepath: str) -> list:
    """
    Loads target groups from links.json supporting both structured 'groups' schema
    and legacy 'target_links' array. Returns list of group dicts.
    """
    data = load_from_file(filepath)
    if not isinstance(data, dict):
        return []

    if "groups" in data and isinstance(data["groups"], list):
        groups = []
        for item in data["groups"]:
            if isinstance(item, dict) and item.get("url"):
                groups.append({
                    "url": item["url"],
                    "name": item.get("name", "—"),
                    "members": item.get("members", "—"),
                    "privacy": item.get("privacy", "—"),
                    "membership": item.get("membership"),
                    "updated_at": item.get("updated_at"),
                })
            elif isinstance(item, str) and item.strip():
                groups.append({
                    "url": item.strip(),
                    "name": "—",
                    "members": "—",
                    "privacy": "—",
                    "updated_at": None,
                })
        return groups

    if "target_links" in data and isinstance(data["target_links"], list):
        return [
            {"url": u, "name": "—", "members": "—", "privacy": "—", "updated_at": None}
            for u in data["target_links"] if isinstance(u, str) and u.strip()
        ]
    return []


def save_group_targets(filepath: str, groups: list) -> bool:
    """
    Persists target groups into links.json with structured metadata and
    a synchronized backward-compatible target_links array.
    """
    try:
        target_path = str(filepath)
        if not os.path.isabs(target_path):
            if os.path.exists(target_path):
                pass
            elif target_path.startswith("repo/") or target_path.startswith("repo\\"):
                candidate = os.path.join(BASE_DIR, target_path[5:])
                target_path = candidate
            else:
                target_path = os.path.join(BASE_DIR, target_path)

        os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)

        clean_groups = []
        clean_links = []
        for g in groups:
            if isinstance(g, dict) and g.get("url"):
                url = g["url"]
                clean_groups.append({
                    "url": url,
                    "name": g.get("name", "—"),
                    "members": g.get("members", "—"),
                    "privacy": g.get("privacy", "—"),
                    "membership": g.get("membership"),
                    "updated_at": g.get("updated_at"),
                })
                clean_links.append(url)
            elif isinstance(g, str) and g.strip():
                clean_groups.append({
                    "url": g.strip(),
                    "name": "—",
                    "members": "—",
                    "privacy": "—",
                    "updated_at": None,
                })
                clean_links.append(g.strip())

        payload = {
            "groups": clean_groups,
            "target_links": clean_links,
        }
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=4, ensure_ascii=False)
        return True
    except Exception:
        return False


def load_from_file(filepath: str):
    if not os.path.isabs(filepath):
        if not os.path.exists(filepath):
            if filepath.startswith("repo/") or filepath.startswith("repo\\"):
                candidate = os.path.join(BASE_DIR, filepath[5:])
                if os.path.exists(candidate):
                    filepath = candidate
            else:
                candidate_path = os.path.join(BASE_DIR, filepath)
                if os.path.exists(candidate_path):
                    filepath = candidate_path
    if not os.path.exists(filepath):
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data
    except Exception:
        return {}
