import aiohttp
from typing import Tuple

CATBOX_API = "https://catbox.moe/user/api.php"
IMGDB_API = "https://imgdb.io/api/v1/upload"

def format_file_size(size_bytes: int) -> str:
    """Formats bytes into human readable KB, MB, GB."""
    if size_bytes >= 1024 ** 3:
        return f"{size_bytes / (1024 ** 3):.2f} GB"
    elif size_bytes >= 1024 ** 2:
        return f"{size_bytes / (1024 ** 2):.1f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"

async def upload_to_catbox(session: aiohttp.ClientSession, file_bytes: bytes, filename: str, mime_type: str) -> str:
    """Uploads file to Catbox.moe (supports up to 200MB, permanent hosting)."""
    data = aiohttp.FormData()
    data.add_field("reqtype", "fileupload")
    data.add_field(
        "fileToUpload",
        file_bytes,
        filename=filename,
        content_type=mime_type
    )

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    async with session.post(CATBOX_API, data=data, headers=headers, timeout=aiohttp.ClientTimeout(total=45)) as resp:
        if resp.status == 200:
            link = (await resp.text()).strip()
            if link.startswith("http"):
                return link
            raise ValueError(f"Catbox error: {link}")
        raise RuntimeError(f"Catbox HTTP {resp.status}")

async def upload_to_imgdb(session: aiohttp.ClientSession, file_bytes: bytes, filename: str, mime_type: str) -> str:
    """Uploads file to imgdb.io (fast image mirror)."""
    data = aiohttp.FormData()
    data.add_field(
        "file",
        file_bytes,
        filename=filename,
        content_type=mime_type
    )

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    async with session.post(IMGDB_API, data=data, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        if resp.status in (200, 201):
            res_json = await resp.json()
            url = res_json.get("url")
            if url:
                return url
            raise ValueError("imgdb missing url")
        raise RuntimeError(f"imgdb HTTP {resp.status}")

async def smart_upload(session: aiohttp.ClientSession, file_bytes: bytes, filename: str, mime_type: str, preferred: str = "catbox") -> Tuple[str, str]:
    """
    Uploads using preferred provider, automatically falling back to secondary if primary fails.
    Returns: (direct_url, provider_name)
    """
    providers = ["catbox", "imgdb"] if preferred == "catbox" else ["imgdb", "catbox"]
    errors = []

    for prov in providers:
        try:
            if prov == "catbox":
                url = await upload_to_catbox(session, file_bytes, filename, mime_type)
                return url, "Catbox.moe"
            elif prov == "imgdb":
                url = await upload_to_imgdb(session, file_bytes, filename, mime_type)
                return url, "imgdb.io"
        except Exception as e:
            errors.append(f"{prov}: {e}")
            continue

    raise RuntimeError("All image providers failed: " + "; ".join(errors))

def generate_embed_codes(url: str, alt_text: str = "image") -> dict:
    """Generates ready-to-copy Markdown, HTML, and BBCode embeds."""
    return {
        "direct": url,
        "markdown": f"![{alt_text}]({url})",
        "html": f'<img src="{url}" alt="{alt_text}" />',
        "bbcode": f"[img]{url}[/img]"
    }
