const COMMAND = "最新资讯消息";
const DEFAULT_WORKFLOW_FILE = "daily-feishu.yml";
const DEFAULT_REF = "main";
const RECENT_EVENT_TTL_MS = 10 * 60 * 1000;
const recentEvents = new Map();

export default {
  async fetch(request, env) {
    return handleRequest(request, env);
  },
};

export async function handleRequest(request, env) {
  if (request.method !== "POST") {
    return jsonResponse({ error: "method_not_allowed" }, 405);
  }

  let payload;
  try {
    payload = await request.json();
  } catch {
    return jsonResponse({ error: "invalid_json" }, 400);
  }

  if (payload.encrypt) {
    return jsonResponse({ error: "encrypted_events_are_not_supported" }, 400);
  }

  const challenge = handleUrlVerification(payload, env);
  if (challenge) {
    return challenge;
  }

  if (!verifyFeishuToken(payload, env)) {
    return jsonResponse({ error: "invalid_feishu_token" }, 403);
  }

  const eventType = getEventType(payload);
  if (eventType !== "im.message.receive_v1") {
    return jsonResponse({ status: "ignored", reason: "unsupported_event" });
  }

  const eventId = getEventId(payload);
  if (eventId && seenRecently(eventId)) {
    return jsonResponse({ status: "ignored", reason: "duplicate_event" });
  }

  const text = extractCommandText(payload);
  if (!isCommand(text)) {
    return jsonResponse({ status: "ignored", reason: "command_not_matched" });
  }

  try {
    await dispatchGitHubWorkflow(env, { eventId, text });
  } catch (error) {
    return jsonResponse(
      {
        error: "github_dispatch_failed",
        message: redactErrorMessage(error),
      },
      502,
    );
  }

  return jsonResponse({ status: "accepted", command: COMMAND });
}

export function handleUrlVerification(payload, env) {
  if (payload.type !== "url_verification" || !payload.challenge) {
    return null;
  }
  if (!env.FEISHU_VERIFICATION_TOKEN || payload.token !== env.FEISHU_VERIFICATION_TOKEN) {
    return jsonResponse({ error: "invalid_feishu_token" }, 403);
  }
  return jsonResponse({ challenge: payload.challenge });
}

export function verifyFeishuToken(payload, env) {
  if (!env.FEISHU_VERIFICATION_TOKEN) {
    return false;
  }
  const token = payload?.header?.token ?? payload?.token;
  return token === env.FEISHU_VERIFICATION_TOKEN;
}

export function getEventType(payload) {
  return payload?.header?.event_type ?? payload?.event?.type ?? "";
}

export function getEventId(payload) {
  return payload?.header?.event_id ?? payload?.event_id ?? "";
}

export function extractCommandText(payload) {
  const content = payload?.event?.message?.content;
  if (typeof content === "string") {
    try {
      const parsed = JSON.parse(content);
      return normalizeText(parsed.text ?? "");
    } catch {
      return normalizeText(content);
    }
  }
  if (content && typeof content === "object") {
    return normalizeText(content.text ?? "");
  }
  return "";
}

export function isCommand(text) {
  const normalized = normalizeText(text);
  return normalized === COMMAND || normalized.includes(` ${COMMAND}`);
}

export async function dispatchGitHubWorkflow(env, options = {}) {
  const required = ["GITHUB_OWNER", "GITHUB_REPO", "GITHUB_TOKEN"];
  const missing = required.filter((name) => !env[name]);
  if (missing.length > 0) {
    throw new Error(`missing environment variables: ${missing.join(", ")}`);
  }

  const workflowFile = env.GITHUB_WORKFLOW_FILE || DEFAULT_WORKFLOW_FILE;
  const ref = env.GITHUB_REF || DEFAULT_REF;
  const url = new URL(
    `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}` +
      `/actions/workflows/${workflowFile}/dispatches`,
  );

  const response = await fetch(url, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "Content-Type": "application/json",
      "User-Agent": "feishu-command-router",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify({
      ref,
      inputs: {
        trigger_source: "feishu_command",
        command: COMMAND,
        feishu_event_id: options.eventId || "",
      },
    }),
  });

  if (response.status !== 204) {
    const body = await response.text();
    throw new Error(`GitHub API returned ${response.status}: ${body.slice(0, 300)}`);
  }
}

function seenRecently(eventId) {
  const now = Date.now();
  for (const [key, expiresAt] of recentEvents) {
    if (expiresAt <= now) {
      recentEvents.delete(key);
    }
  }
  if (recentEvents.has(eventId)) {
    return true;
  }
  recentEvents.set(eventId, now + RECENT_EVENT_TTL_MS);
  return false;
}

function normalizeText(value) {
  return String(value)
    .replace(/<at[^>]*>.*?<\/at>/g, " ")
    .replace(/@\S+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function redactErrorMessage(error) {
  return String(error?.message || error)
    .replace(/github_pat_[A-Za-z0-9_]+/g, "[REDACTED_TOKEN]")
    .replace(/ghp_[A-Za-z0-9_]+/g, "[REDACTED_TOKEN]")
    .slice(0, 500);
}

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
    },
  });
}
