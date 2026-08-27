const followupForm = document.querySelector("[data-followup-chat]");

if (followupForm) {
  const stream = document.querySelector("[data-conversation-stream]");
  const input = followupForm.querySelector("[data-chat-input]");
  const submit = followupForm.querySelector("[data-chat-submit]");
  const formError = followupForm.querySelector("[data-chat-error]");
  const messageCount = document.querySelector("[data-message-count]");

  const scrollToLatest = () => {
    if (stream) stream.scrollTop = stream.scrollHeight;
  };

  const incrementMessageCount = () => {
    if (!messageCount) return;
    const current = Number.parseInt(messageCount.textContent || "0", 10) || 0;
    messageCount.textContent = `${current + 2} 条`;
  };

  const createMessage = (role, content, status = "completed") => {
    const article = document.createElement("article");
    article.className = `conversation-message conversation-${role}`;

    const meta = document.createElement("div");
    meta.className = "conversation-meta";
    const author = document.createElement("strong");
    author.textContent = role === "user" ? "你" : "Paper Hunter";
    const phase = document.createElement("span");
    phase.textContent = "followup";
    meta.append(author, phase);

    const statusNode = document.createElement("span");
    statusNode.className = `status status-message-${status}`;
    statusNode.dataset.messageStatus = "";
    statusNode.textContent = status;
    if (role === "assistant") meta.append(statusNode);

    const contentNode = document.createElement("div");
    contentNode.className = "conversation-content";
    contentNode.dataset.messageContent = "";
    contentNode.textContent = content;
    article.append(meta, contentNode);
    stream?.append(article);
    scrollToLatest();
    return { article, contentNode, statusNode };
  };

  const setStatus = (message, status, waitingText) => {
    message.statusNode.className = `status status-message-${status}`;
    message.statusNode.textContent = status;
    message.article.classList.toggle(
      "conversation-message-streaming",
      ["pending", "retrieving", "generating", "streaming"].includes(status),
    );
    if (waitingText && !message.article.dataset.hasDelta) {
      message.contentNode.textContent = waitingText;
    }
  };

  const appendFailure = (message, detail) => {
    setStatus(message, "failed");
    const error = document.createElement("small");
    error.className = "tool-error";
    error.textContent = detail || "回答生成失败，请重新发送。";
    message.article.append(error);
  };

  const parseEventBlock = (block) => {
    let event = "message";
    const dataLines = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    }
    if (!dataLines.length) return null;
    return { event, payload: JSON.parse(dataLines.join("\n")) };
  };

  const consumeResponse = async (response, assistantMessage) => {
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `请求失败（HTTP ${response.status}）`);
    }
    if (!response.body) throw new Error("浏览器没有收到可读取的流式响应");

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let doneReceived = false;
    let streamError = null;

    const handleEvent = ({ event, payload }) => {
      if (event === "status") {
        const labels = {
          retrieving: "正在检索所选论文证据...",
          generating: "正在生成回答...",
        };
        setStatus(assistantMessage, payload.status, labels[payload.status]);
        if (payload.message_id) {
          assistantMessage.article.dataset.messageId = String(payload.message_id);
        }
      } else if (event === "delta") {
        if (!assistantMessage.article.dataset.hasDelta) {
          assistantMessage.article.dataset.hasDelta = "true";
          assistantMessage.contentNode.textContent = "";
        }
        setStatus(assistantMessage, "streaming");
        assistantMessage.contentNode.textContent += payload.content || "";
        scrollToLatest();
      } else if (event === "done") {
        doneReceived = true;
        setStatus(assistantMessage, "completed");
        if (payload.message_id) {
          assistantMessage.article.dataset.messageId = String(payload.message_id);
        }
        if (payload.model) {
          const model = document.createElement("small");
          model.textContent = `模型：${payload.model}`;
          assistantMessage.article.append(model);
        }
        if (Number(payload.evidence_count) > 0) {
          const evidence = document.createElement("small");
          evidence.textContent = `证据：${payload.evidence_count} 条`;
          assistantMessage.article.append(evidence);
        }
      } else if (event === "error") {
        streamError = new Error(payload.message || "回答生成失败");
      }
    };

    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      buffer = buffer.replaceAll("\r\n", "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary).trim();
        buffer = buffer.slice(boundary + 2);
        if (block) {
          const parsed = parseEventBlock(block);
          if (parsed) handleEvent(parsed);
        }
        boundary = buffer.indexOf("\n\n");
      }
      if (streamError) throw streamError;
      if (done) break;
    }

    if (!doneReceived) throw new Error("流式回答意外结束，请重新发送问题");
  };

  followupForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = input.value.replace(/\s+/g, " ").trim();
    if (!question || submit.disabled) return;

    formError.hidden = true;
    formError.textContent = "";
    createMessage("user", question);
    const assistantMessage = createMessage("assistant", "正在分析...", "pending");
    assistantMessage.article.classList.add("conversation-message-streaming");
    incrementMessageCount();
    input.value = "";
    input.disabled = true;
    submit.disabled = true;

    try {
      const body = new FormData();
      body.set("message", question);
      const response = await fetch(followupForm.dataset.streamUrl, {
        method: "POST",
        body,
        headers: { Accept: "text/event-stream" },
      });
      await consumeResponse(response, assistantMessage);
    } catch (error) {
      const detail = error instanceof Error ? error.message : "回答生成失败";
      appendFailure(assistantMessage, detail);
      formError.textContent = detail;
      formError.hidden = false;
    } finally {
      input.disabled = false;
      submit.disabled = false;
      input.focus();
      scrollToLatest();
    }
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      followupForm.requestSubmit();
    }
  });

  scrollToLatest();
}
