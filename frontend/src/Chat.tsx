import { useEffect, useRef, useState, type FormEvent } from "react";
import Markdown from "react-markdown";
import {
  api,
  ApiError,
  type ChatMessage,
  type Conversation,
  type Preference,
  type ToolCall,
  type User,
} from "./api";

type Props = {
  user: User;
  prefs: Preference[];
  setPrefs: (p: Preference[]) => void;
  onLogout: () => void;
};

const STARTERS = [
  "I'm vegetarian. What's good for lunch under 40 SAR?",
  "I can't have dairy. Suggest a dinner.",
  "Something spicy for a snack",
];

const KIND_LABELS: Record<string, string> = {
  allergy: "Allergies",
  diet: "Diet",
  spice: "Spice level",
  budget: "Budget",
  like: "Likes",
  dislike: "Dislikes",
  other: "Other notes",
};
const KIND_ORDER = Object.keys(KIND_LABELS);
const SPICE_WORDS = ["No spice", "Mild", "Medium", "Hot"];

function prettyValue(p: Preference): string {
  if (p.kind === "allergy") return p.value.replace("_", " ");
  if (p.kind === "spice") return SPICE_WORDS[Number(p.value)] ?? p.value;
  if (p.kind === "budget") return `Up to ${p.value} SAR`;
  return p.value;
}

// One plain-language line per tool call, for the "what the assistant checked" list.
function describeCall(call: ToolCall): string {
  let args: Record<string, unknown> = {};
  let out: Record<string, unknown> = {};
  try {
    args = JSON.parse(call.arguments || "{}");
    out = JSON.parse(call.output || "{}");
  } catch {
    return call.name;
  }
  if (out.error) return `${call.name} failed: ${out.error}`;
  if (call.name === "search_menu") {
    const parts: string[] = [];
    if (args.category) parts.push(String(args.category));
    if (args.query) parts.push(`"${args.query}"`);
    if (Array.isArray(args.exclude_allergens) && args.exclude_allergens.length)
      parts.push(`without ${args.exclude_allergens.join(", ").replace(/_/g, " ")}`);
    if (args.vegetarian) parts.push("vegetarian");
    if (args.vegan) parts.push("vegan");
    if (args.max_spice !== undefined) parts.push(`spice up to ${args.max_spice}`);
    if (args.min_spice !== undefined) parts.push(`spice from ${args.min_spice}`);
    if (args.max_price !== undefined) parts.push(`up to ${args.max_price} SAR`);
    const filters = parts.length ? parts.join(", ") : "whole menu";
    return `Searched the menu (${filters}) and found ${out.count ?? 0}`;
  }
  const pref = (out.saved ?? out.updated ?? out.removed) as Preference | undefined;
  const label = (p: Preference) => `${p.kind === "spice" ? "spice level" : p.kind}: ${prettyValue(p)}`;
  if (call.name === "save_preference" && pref) return `Remembered ${label(pref)}`;
  if (call.name === "update_preference" && pref) return `Changed ${label(pref)}`;
  if (call.name === "remove_preference" && pref) return `Forgot ${label(pref)}`;
  return call.name;
}

export default function Chat({ user, prefs, setPrefs, onLogout }: Props) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [panel, setPanel] = useState<"none" | "chats" | "memory">("none");
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    api.conversations().then(setConversations).catch(() => setConversations([]));
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, sending]);

  function startNew() {
    setActiveId(null);
    setMessages([]);
    setError(null);
    setPanel("none");
    inputRef.current?.focus();
  }

  async function openConversation(id: number) {
    setError(null);
    setPanel("none");
    try {
      const msgs = await api.messages(id);
      setActiveId(id);
      setMessages(msgs);
    } catch {
      setError("That conversation couldn't be opened.");
    }
  }

  async function send(text: string) {
    const message = text.trim();
    if (!message || sending) return;
    setError(null);
    setDraft("");
    setMessages((m) => [...m, { role: "user", content: message }]);
    setSending(true);
    try {
      const res = await api.chat(message, activeId);
      setMessages((m) => [...m, { role: "assistant", content: res.reply, toolCalls: res.tool_calls }]);
      setPrefs(res.preferences);
      if (activeId === null) {
        setActiveId(res.conversation_id);
        api.conversations().then(setConversations).catch(() => {});
      }
    } catch (err) {
      // Put the text back so nothing typed is lost.
      setMessages((m) => m.slice(0, -1));
      setDraft(message);
      if (err instanceof ApiError && err.status === 401) onLogout();
      else setError(err instanceof ApiError ? err.message : "Can't reach Sufra. Check your connection and send again.");
    } finally {
      setSending(false);
    }
  }

  async function forget(p: Preference) {
    try {
      await api.forget(p.id);
      setPrefs(prefs.filter((x) => x.id !== p.id));
    } catch {
      setError(`Couldn't forget "${prettyValue(p)}". Try again.`);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    send(draft);
  }

  const grouped = KIND_ORDER.map((kind) => ({
    kind,
    items: prefs.filter((p) => p.kind === kind),
  })).filter((g) => g.items.length);

  return (
    <div className={`shell panel-${panel}`}>
      <aside className="sidebar" aria-label="Conversations">
        <div className="sadu sadu-vertical" aria-hidden="true" />
        <div className="sidebar-inner">
          <h1 className="wordmark small">
            Sufra <span lang="ar">سفرة</span>
          </h1>
          <button className="new-chat" onClick={startNew}>
            New conversation
          </button>
          <nav className="conv-list">
            {conversations.length === 0 && <p className="muted">Your conversations will appear here.</p>}
            {conversations.map((c) => (
              <button
                key={c.id}
                className="conv"
                aria-current={c.id === activeId ? "true" : undefined}
                onClick={() => openConversation(c.id)}
              >
                {c.title || "Untitled"}
              </button>
            ))}
          </nav>
          <div className="account">
            <span>Signed in as {user.username}</span>
            <button className="link" onClick={onLogout}>
              Sign out
            </button>
          </div>
        </div>
      </aside>

      <main className="chat">
        <header className="chat-top">
          <button className="ghost only-mobile" onClick={() => setPanel(panel === "chats" ? "none" : "chats")}>
            Chats
          </button>
          <span className="only-mobile wordmark tiny">Sufra</span>
          <button className="ghost only-mobile" onClick={() => setPanel(panel === "memory" ? "none" : "memory")}>
            Memory ({prefs.length})
          </button>
        </header>

        <div className="messages" aria-live="polite">
          {messages.length === 0 && (
            <div className="empty">
              <h2>What are you in the mood for, {user.username}?</h2>
              <p>
                Breakfast, lunch, dinner or something small. Mention allergies or dislikes once and they'll be
                remembered.
              </p>
              <div className="starters">
                {STARTERS.map((s) => (
                  <button key={s} onClick={() => send(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, i) => (
            <article key={i} className={`msg msg-${m.role}`}>
              {m.role === "assistant" ? <Markdown>{m.content}</Markdown> : <p>{m.content}</p>}
              {m.toolCalls && m.toolCalls.length > 0 && (
                <details className="checks">
                  <summary>
                    What the assistant checked ({m.toolCalls.length})
                  </summary>
                  <ul>
                    {m.toolCalls.map((c, j) => (
                      <li key={j}>
                        {describeCall(c)}
                        <code>
                          {c.name}({c.arguments})
                        </code>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              {m.role === "assistant" && m.toolCalls && m.toolCalls.length === 0 && (
                <p className="no-checks">Answered without checking the menu.</p>
              )}
            </article>
          ))}

          {sending && (
            <div className="msg msg-assistant thinking" role="status">
              <span />
              <span />
              <span />
              <span className="sr-only">The assistant is replying</span>
            </div>
          )}
          <div ref={endRef} />
        </div>

        {error && (
          <p className="chat-error" role="alert">
            {error}
          </p>
        )}

        <form className="composer" onSubmit={onSubmit}>
          <label htmlFor="draft" className="sr-only">
            Message
          </label>
          <textarea
            id="draft"
            ref={inputRef}
            value={draft}
            rows={1}
            maxLength={2000}
            placeholder="Ask for a recommendation, or tell me what you like"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(draft);
              }
            }}
          />
          <button className="primary" type="submit" disabled={sending || !draft.trim()}>
            Send
          </button>
        </form>
      </main>

      <aside className="memory" aria-label="What Sufra remembers">
        <h2>What I remember</h2>
        {grouped.length === 0 ? (
          <p className="muted">
            Nothing yet. Mention an allergy, a diet, or a dish you love and it will show up here.
          </p>
        ) : (
          grouped.map((g) => (
            <section key={g.kind} className={`pref-group kind-${g.kind}`}>
              <h3>{KIND_LABELS[g.kind]}</h3>
              <ul>
                {g.items.map((p) => (
                  <li key={p.id} className="pref">
                    <span>{prettyValue(p)}</span>
                    <button
                      className="forget"
                      onClick={() => forget(p)}
                      aria-label={`Forget ${KIND_LABELS[p.kind].toLowerCase()}: ${prettyValue(p)}`}
                      title="Forget this"
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ))
        )}
        <p className="memory-note">To change something, say so in the chat or remove it here.</p>
      </aside>
    </div>
  );
}
