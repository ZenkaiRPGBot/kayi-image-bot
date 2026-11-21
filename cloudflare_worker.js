/**
 * Kayi Image to Link Bot — Cloudflare Worker (Serverless Edition)
 * 100% Free Forever · Zero Render Hours · 0ms Edge Latency
 */

const CATBOX_API = "https://catbox.moe/user/api.php";

async function tgRequest(botToken, method, payload) {
  const url = `https://api.telegram.org/bot${botToken}/${method}`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  return await res.json();
}

async function sendTg(botToken, chatId, text, options = {}) {
  const payload = {
    chat_id: chatId,
    text: text,
    parse_mode: options.parse_mode || "HTML",
    disable_web_page_preview: options.disable_web_page_preview ?? true
  };
  if (options.reply_markup) payload.reply_markup = options.reply_markup;
  return await tgRequest(botToken, "sendMessage", payload);
}

function formatBytes(bytes) {
  if (bytes >= 1024 ** 3) return (bytes / (1024 ** 3)).toFixed(2) + " GB";
  if (bytes >= 1024 ** 2) return (bytes / (1024 ** 2)).toFixed(1) + " MB";
  if (bytes >= 1024) return (bytes / 1024).toFixed(1) + " KB";
  return bytes + " B";
}

async function uploadToCatbox(fileBlob, filename) {
  const fd = new FormData();
  fd.append("reqtype", "fileupload");
  fd.append("fileToUpload", fileBlob, filename);

  const resp = await fetch(CATBOX_API, {
    method: "POST",
    headers: {
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    },
    body: fd
  });

  if (resp.ok) {
    const link = (await resp.text()).trim();
    if (link.startsWith("http")) return link;
  }
  throw new Error("Catbox upload failed");
}

export default {
  async fetch(request, env) {
    const BOT_TOKEN = env.BOT_TOKEN;
    if (!BOT_TOKEN) return new Response("Missing BOT_TOKEN secret.", { status: 500 });

    if (request.method === "GET") {
      return new Response("Kayi Image to Link Cloudflare Worker is healthy & operational.", { status: 200 });
    }

    if (request.method !== "POST") {
      return new Response("Method Not Allowed", { status: 405 });
    }

    try {
      const update = await request.json();
      const message = update.message;
      if (!message) return new Response("OK", { status: 200 });

      const chatId = message.chat.id;
      const text = message.text ? message.text.trim() : "";
      const colo = request.cf?.colo || "EDGE";

      // 1. /start command
      if (text === "/start") {
        const welcome = `<b>Welcome to Kayi Image to Link Bot!</b> 🚀\n\nConvert any photo, document, sticker, or GIF into a **permanent high-speed direct link** with 1-tap copy blocks.\n\n<b>Features:</b>\n• Permanent Cloud Hosting (Never expires)\n• Separate 1-tap copy blocks for Direct URL & Markdown\n• Ready-to-use HTML & BBCode embeds\n\n👉 <b>Send or forward any image below to get started!</b>`;
        const markup = {
          inline_keyboard: [
            [{ text: "Kayi TV Channel 👑", url: "https://t.me/kayitv" }]
          ]
        };
        await sendTg(BOT_TOKEN, chatId, welcome, { reply_markup: markup });
        return new Response("OK", { status: 200 });
      }

      // 2. /ping command
      if (text === "/ping") {
        await sendTg(BOT_TOKEN, chatId, `<b>Pong!</b> ⚡\n\n<b>Latency:</b> <code>~0 ms (Edge Webhook)</code>\n\n<b>Edge Node:</b> <code>${colo}</code>`);
        return new Response("OK", { status: 200 });
      }

      // 3. /usage command (Owner Only)
      if (text === "/usage" || text === "/render" || text === "/status") {
        if (message.from?.id !== 986380678) {
          await sendTg(BOT_TOKEN, chatId, "Access restricted to bot owner.");
          return new Response("OK", { status: 200 });
        }
        const usageMsg = `<b>Kayi Image to Link — Cloudflare Edge Metrics</b>\n\n<b>Hosting & Infrastructure:</b>\n• <b>Platform:</b> Cloudflare Workers (Serverless)\n• <b>Edge Data Center:</b> <code>${colo}</code>\n• <b>Plan:</b> 100% Free Forever\n• <b>Free Limit:</b> 100,000 requests/day\n• <b>Server Hours Used:</b> <code>0.0h (No hour limits)</code>\n\n<b>Status:</b> 🟢 Always Online · 0ms Cold Start`;
        await sendTg(BOT_TOKEN, chatId, usageMsg);
        return new Response("OK", { status: 200 });
      }

      // 4. Handle Media Upload (Photo, Document, Animation, Sticker)
      let fileId = null;
      let filename = "image.png";
      let fileSize = 0;

      if (message.photo && message.photo.length > 0) {
        const ph = message.photo[message.photo.length - 1];
        fileId = ph.file_id;
        fileSize = ph.file_size || 0;
        filename = `photo_${ph.file_unique_id}.jpg`;
      } else if (message.document) {
        const doc = message.document;
        fileId = doc.file_id;
        fileSize = doc.file_size || 0;
        filename = doc.file_name || `file_${doc.file_unique_id}.png`;
      } else if (message.animation) {
        const anim = message.animation;
        fileId = anim.file_id;
        fileSize = anim.file_size || 0;
        filename = anim.file_name || `animation_${anim.file_unique_id}.mp4`;
      } else if (message.sticker) {
        const st = message.sticker;
        fileId = st.file_id;
        fileSize = st.file_size || 0;
        filename = `sticker_${st.file_unique_id}.webp`;
      }

      if (fileId) {
        // Fetch file path from Telegram
        const fileInfo = await tgRequest(BOT_TOKEN, "getFile", { file_id: fileId });
        if (!fileInfo.ok || !fileInfo.result.file_path) {
          await sendTg(BOT_TOKEN, chatId, "❌ <i>Failed to fetch image from Telegram servers.</i>");
          return new Response("OK", { status: 200 });
        }

        const downloadUrl = `https://api.telegram.org/file/bot${BOT_TOKEN}/${fileInfo.result.file_path}`;
        const fileResp = await fetch(downloadUrl);
        const fileBlob = await fileResp.blob();

        // Upload to Catbox
        const directUrl = await uploadToCatbox(fileBlob, filename);
        const sizeStr = formatBytes(fileBlob.size || fileSize);

        // 1. Separate Direct Link header & 1-tap copy block
        await sendTg(BOT_TOKEN, chatId, "<b>Direct Image Link 👇</b>");
        await sendTg(BOT_TOKEN, chatId, `<code>${directUrl}</code>`);

        // 2. Separate Markdown header & 1-tap copy block
        await sendTg(BOT_TOKEN, chatId, "<b>Markdown Embed 👇</b>");
        await sendTg(BOT_TOKEN, chatId, `<code>![image](${directUrl})</code>`);

        // 3. Summary Card with colorful interactive buttons
        const cardText = `<b>Upload Complete ✅</b>\n\n• <b>File:</b> <code>${filename}</code>\n• <b>Size:</b> <code>${sizeStr}</code>\n• <b>Host:</b> <code>Catbox.moe</code> (Permanent)\n\n<i>Tap below to open or view:</i>`;

        const markup = {
          inline_keyboard: [
            [
              { text: "Open Image 🌐", url: directUrl },
              { text: "Kayi TV Channel 👑", url: "https://t.me/kayitv" }
            ]
          ]
        };

        await sendTg(BOT_TOKEN, chatId, cardText, { reply_markup: markup });
        return new Response("OK", { status: 200 });
      }

      return new Response("OK", { status: 200 });
    } catch (err) {
      console.error(`Error: ${err}`);
      return new Response("OK", { status: 200 });
    }
  }
};
