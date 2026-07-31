import assert from "node:assert/strict";
import test from "node:test";

import {
  extractCommandText,
  handleRequest,
  isCommand,
} from "../src/index.js";

const baseEnv = {
  FEISHU_VERIFICATION_TOKEN: "verify-token",
  GITHUB_OWNER: "owner",
  GITHUB_REPO: "repo",
  GITHUB_TOKEN: "github-token",
  GITHUB_WORKFLOW_FILE: "daily-feishu.yml",
  GITHUB_REF: "main",
};

test("url verification returns challenge", async () => {
  const request = jsonRequest({
    type: "url_verification",
    token: "verify-token",
    challenge: "challenge-value",
  });

  const response = await handleRequest(request, baseEnv);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { challenge: "challenge-value" });
});

test("invalid token is rejected", async () => {
  const response = await handleRequest(
    jsonRequest({
      header: {
        token: "wrong-token",
        event_type: "im.message.receive_v1",
      },
    }),
    baseEnv,
  );

  assert.equal(response.status, 403);
});

test("extracts command from feishu text message content", () => {
  const payload = {
    event: {
      message: {
        content: JSON.stringify({ text: "@机器人 最新资讯消息" }),
      },
    },
  };

  assert.equal(extractCommandText(payload), "最新资讯消息");
  assert.equal(isCommand(extractCommandText(payload)), true);
});

test("non-command message is ignored", async () => {
  const response = await handleRequest(
    jsonRequest(messageEvent({ text: "你好" })),
    baseEnv,
  );

  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    status: "ignored",
    reason: "command_not_matched",
  });
});

test("command dispatches github workflow", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls.push({ url: String(url), init });
    return new Response(null, { status: 204 });
  });

  const response = await handleRequest(
    jsonRequest(messageEvent({ text: "最新资讯消息", eventId: "event-1" })),
    baseEnv,
  );

  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    status: "accepted",
    command: "最新资讯消息",
  });
  assert.equal(calls.length, 1);
  assert.match(
    calls[0].url,
    /\/repos\/owner\/repo\/actions\/workflows\/daily-feishu\.yml\/dispatches$/,
  );
  const body = JSON.parse(calls[0].init.body);
  assert.equal(body.ref, "main");
  assert.equal(body.inputs.trigger_source, "feishu_command");
  assert.equal(body.inputs.command, "最新资讯消息");
  assert.equal(body.inputs.feishu_event_id, "event-1");
});

function messageEvent({ text, eventId = "event-id" }) {
  return {
    schema: "2.0",
    header: {
      event_id: eventId,
      event_type: "im.message.receive_v1",
      token: "verify-token",
    },
    event: {
      message: {
        message_type: "text",
        content: JSON.stringify({ text }),
      },
    },
  };
}

function jsonRequest(payload) {
  return new Request("https://worker.example.com/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}
