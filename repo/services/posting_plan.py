"""
Posting Plan & Multi-Post Allocation Service.

Implements data models for multiple posts and round-robin allocation
across eligible Facebook target groups, respecting 24h cooldown and batch limits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.state_manager import StateManager


@dataclass
class PostItem:
    """Represents an individual post draft or ready item."""
    id: str
    name: str
    content: str
    images: List[str] = field(default_factory=list)
    is_selected: bool = True
    status: str = "ready"  # "draft" | "ready"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "content": self.content,
            "images": list(self.images),
            "is_selected": self.is_selected,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], default_id: str = "post_1") -> "PostItem":
        return cls(
            id=str(data.get("id", default_id)),
            name=str(data.get("name", "Bài viết chưa đặt tên")),
            content=str(data.get("content", "")),
            images=list(data.get("images", [])),
            is_selected=bool(data.get("is_selected", True)),
            status=str(data.get("status", "ready")),
        )


@dataclass
class PostingTask:
    """Represents a concrete assignment of one post to one target group."""
    index: int
    group_url: str
    group_name: str
    post_id: str
    post_name: str
    content: str
    images: List[str]


@dataclass
class PostingPlan:
    """Complete planned execution schedule for a session."""
    tasks: List[PostingTask]
    total_runs: int
    selected_posts_count: int
    eligible_groups_count: int
    skipped_cooldown_count: int


def load_posts_collection(posts_file: Path | str) -> List[PostItem]:
    """
    Loads posts from posts.json.
    Provides transparent backward compatibility: if posts.json has legacy
    schema ({"content": "...", "images": [...]}), automatically wraps it as post_1.
    """
    path = Path(posts_file)
    if not path.is_file():
        return [
            PostItem(
                id="post_1",
                name="Bài 01 Mặc định",
                content="",
                images=[],
                is_selected=True,
                status="draft",
            )
        ]

    try:
        raw_text = path.read_text(encoding="utf-8").strip()
        if not raw_text:
            return []
        data = json.loads(raw_text)
    except Exception:
        return []

    if isinstance(data, dict):
        if "posts" in data and isinstance(data["posts"], list):
            items = []
            for idx, p in enumerate(data["posts"], 1):
                if isinstance(p, dict):
                    items.append(PostItem.from_dict(p, default_id=f"post_{idx}"))
            return items
        # Legacy format backward compatibility
        legacy_content = str(data.get("content", data.get("post_content", "")))
        legacy_images = list(data.get("images", []))
        return [
            PostItem(
                id="post_1",
                name="Bài 01 Mặc định",
                content=legacy_content,
                images=legacy_images,
                is_selected=True,
                status="ready" if (legacy_content or legacy_images) else "draft",
            )
        ]
    return []


def save_posts_collection(posts_file: Path | str, posts: List[PostItem]) -> None:
    """Persists posts collection to JSON atomically."""
    path = Path(posts_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "posts": [p.to_dict() for p in posts],
        # Also maintain legacy top-level keys for backward compatibility
        "content": posts[0].content if posts else "",
        "images": posts[0].images if posts else [],
    }
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=4, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)


def build_round_robin_plan(
    posts: List[PostItem],
    target_groups: List[str],
    state_manager: Optional[StateManager] = None,
    max_posts_per_run: int = 10,
    cooldown_hours: int = 24,
    groups_meta: Optional[Dict[str, Dict[str, Any]]] = None,
) -> PostingPlan:
    """
    Constructs a deterministic Round-Robin posting plan.
    Cycles through selected valid posts and maps each to an eligible target group.
    """
    groups_meta = groups_meta or {}
    selected_posts = [
        p for p in posts
        if p.is_selected and (p.content.strip() or p.images) and p.status != "draft"
    ]
    # If no ready post is found, fallback to any selected post with content/images
    if not selected_posts:
        selected_posts = [p for p in posts if p.is_selected and (p.content.strip() or p.images)]

    eligible_groups: List[str] = []
    skipped_cooldown = 0

    for url in target_groups:
        cleaned_url = url.strip()
        if not cleaned_url:
            continue
        if state_manager:
            in_cd, _ = state_manager.is_in_cooldown(cleaned_url, cooldown_hours=cooldown_hours)
            if in_cd:
                skipped_cooldown += 1
                continue
        eligible_groups.append(cleaned_url)

    # Cutoff by batch limit
    effective_groups = eligible_groups[:max(0, max_posts_per_run)]
    tasks: List[PostingTask] = []

    if selected_posts and effective_groups:
        num_posts = len(selected_posts)
        for idx, group_url in enumerate(effective_groups, 1):
            assigned_post = selected_posts[(idx - 1) % num_posts]
            meta = groups_meta.get(group_url, {})
            g_name = meta.get("name") or group_url.rstrip("/").split("/")[-1]
            tasks.append(
                PostingTask(
                    index=idx,
                    group_url=group_url,
                    group_name=g_name,
                    post_id=assigned_post.id,
                    post_name=assigned_post.name,
                    content=assigned_post.content,
                    images=list(assigned_post.images),
                )
            )

    return PostingPlan(
        tasks=tasks,
        total_runs=len(tasks),
        selected_posts_count=len(selected_posts),
        eligible_groups_count=len(eligible_groups),
        skipped_cooldown_count=skipped_cooldown,
    )
