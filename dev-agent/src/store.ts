import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import { dirname } from "node:path";
import { mkdirSync } from "node:fs";
import type {
  ChatType,
  InboundEvent,
  ModelSettings,
  OutboxMessage,
  OwnerChatKey,
  SessionRecord,
  SessionState,
  SlotRecord,
} from "./types.ts";

type Row = Record<string, SQLInputValue>;

const SCHEMA = `
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS owners (
  owner_chat_key TEXT PRIMARY KEY CHECK(owner_chat_key GLOB 'private:*' OR owner_chat_key GLOB 'group:*'),
  chat_type TEXT NOT NULL CHECK(chat_type IN ('private', 'group')),
  external_id TEXT NOT NULL,
  unix_uid INTEGER NOT NULL UNIQUE,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS access_rules (
  direction TEXT NOT NULL CHECK(direction IN ('inbound', 'outbound')),
  subject_type TEXT NOT NULL CHECK(subject_type IN ('user', 'group')),
  subject_id TEXT NOT NULL,
  decision TEXT NOT NULL CHECK(decision IN ('allow', 'deny')),
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(direction, subject_type, subject_id)
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  owner_chat_key TEXT NOT NULL,
  session_ref TEXT NOT NULL CHECK(length(session_ref) >= 8),
  task_id TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('active','suspended','open_pr','merged','closed','abandoned')),
  branch TEXT NOT NULL UNIQUE CHECK(branch GLOB 'agent/*'),
  base_sha TEXT NOT NULL,
  workspace TEXT NOT NULL UNIQUE,
  transcript_path TEXT NOT NULL UNIQUE,
  slot_id INTEGER,
  staging_release_id TEXT,
  continuation_of TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(owner_chat_key, session_ref),
  FOREIGN KEY(owner_chat_key) REFERENCES owners(owner_chat_key) ON DELETE RESTRICT,
  FOREIGN KEY(owner_chat_key, continuation_of) REFERENCES sessions(owner_chat_key, session_ref) ON DELETE RESTRICT,
  CHECK(continuation_of IS NULL OR continuation_of <> session_ref)
);

CREATE TABLE IF NOT EXISTS slots (
  slot_id INTEGER PRIMARY KEY CHECK(slot_id BETWEEN 0 AND 4),
  owner_chat_key TEXT,
  session_ref TEXT,
  health TEXT NOT NULL CHECK(health IN ('idle','starting','healthy','degraded')),
  updated_at INTEGER NOT NULL,
  UNIQUE(owner_chat_key),
  FOREIGN KEY(owner_chat_key, session_ref) REFERENCES sessions(owner_chat_key, session_ref) DEFERRABLE INITIALLY DEFERRED,
  CHECK((owner_chat_key IS NULL) = (session_ref IS NULL))
);

CREATE TABLE IF NOT EXISTS inbound_events (
  event_id TEXT PRIMARY KEY,
  owner_chat_key TEXT NOT NULL,
  message_id TEXT NOT NULL,
  received_at INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  FOREIGN KEY(owner_chat_key) REFERENCES owners(owner_chat_key) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS contact_routes (
  owner_chat_key TEXT PRIMARY KEY,
  bot_id TEXT NOT NULL,
  chat_type TEXT NOT NULL CHECK(chat_type IN ('private','group')),
  destination_id TEXT NOT NULL,
  updated_at INTEGER NOT NULL,
  FOREIGN KEY(owner_chat_key) REFERENCES owners(owner_chat_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS staging_releases (
  owner_chat_key TEXT NOT NULL,
  session_ref TEXT NOT NULL,
  release_id TEXT NOT NULL,
  git_sha TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('activating','healthy','failed','retired')),
  validation_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY(owner_chat_key, session_ref, release_id),
  FOREIGN KEY(owner_chat_key, session_ref) REFERENCES sessions(owner_chat_key, session_ref) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pull_requests (
  owner_chat_key TEXT NOT NULL,
  session_ref TEXT NOT NULL,
  number INTEGER NOT NULL,
  url TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('open','merged','closed')),
  head_sha TEXT NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(owner_chat_key, session_ref),
  UNIQUE(number),
  FOREIGN KEY(owner_chat_key, session_ref) REFERENCES sessions(owner_chat_key, session_ref) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS outbox (
  outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_chat_key TEXT NOT NULL,
  session_ref TEXT,
  bot_id TEXT NOT NULL,
  chat_type TEXT NOT NULL CHECK(chat_type IN ('private','group')),
  destination_id TEXT NOT NULL,
  message_json TEXT NOT NULL,
  origin TEXT NOT NULL CHECK(origin IN ('agent','staging','admin','system')),
  state TEXT NOT NULL CHECK(state IN ('pending','leased','delivered')) DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  available_at INTEGER NOT NULL,
  leased_at INTEGER,
  delivered_message_id TEXT,
  last_error TEXT,
  created_at INTEGER NOT NULL,
  FOREIGN KEY(owner_chat_key) REFERENCES owners(owner_chat_key) ON DELETE RESTRICT,
  FOREIGN KEY(owner_chat_key, session_ref) REFERENCES sessions(owner_chat_key, session_ref) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_sessions_owner_updated ON sessions(owner_chat_key, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox(state, available_at, outbox_id);
`;

function sessionFromRow(row: Row): SessionRecord {
  return {
    owner: String(row.owner_chat_key) as OwnerChatKey,
    sessionRef: String(row.session_ref),
    taskId: String(row.task_id),
    title: String(row.title),
    state: String(row.state) as SessionState,
    branch: String(row.branch),
    baseSha: String(row.base_sha),
    workspace: String(row.workspace),
    transcriptPath: String(row.transcript_path),
    slotId: row.slot_id === null ? null : Number(row.slot_id),
    stagingReleaseId: row.staging_release_id === null ? null : String(row.staging_release_id),
    continuationOf: row.continuation_of === null ? null : String(row.continuation_of),
    createdAt: Number(row.created_at),
    updatedAt: Number(row.updated_at),
  };
}

function slotFromRow(row: Row): SlotRecord {
  return {
    id: Number(row.slot_id),
    owner: row.owner_chat_key === null ? null : (String(row.owner_chat_key) as OwnerChatKey),
    sessionRef: row.session_ref === null ? null : String(row.session_ref),
    health: String(row.health) as SlotRecord["health"],
  };
}

export class Store {
  readonly db: DatabaseSync;

  constructor(path = ":memory:", maxSlots = 3) {
    if (path !== ":memory:") mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
    this.db = new DatabaseSync(path);
    this.db.exec(SCHEMA);
    this.migrateSingleRuntimeSchema();
    this.configureSlots(maxSlots);
  }

  private migrateSingleRuntimeSchema(): void {
    const columns = (table: string) =>
      new Set((this.db.prepare(`PRAGMA table_info(${table})`).all() as Row[]).map((row) => String(row.name)));
    if (columns("slots").has("active_color")) {
      this.db.exec("ALTER TABLE slots DROP COLUMN active_color");
    }
    if (columns("staging_releases").has("color")) {
      this.db.exec("ALTER TABLE staging_releases DROP COLUMN color");
    }
  }

  configureSlots(maxSlots: number): void {
    if (!Number.isInteger(maxSlots) || maxSlots < 2 || maxSlots > 3) {
      throw new Error("slot count must be 2 or 3");
    }
    this.transaction(() => {
      this.db
        .prepare(`UPDATE sessions
          SET slot_id = NULL,
              state = CASE WHEN state = 'active' THEN 'suspended' ELSE state END,
              updated_at = ?
          WHERE slot_id >= ?`)
        .run(Date.now(), maxSlots);
      this.db.prepare("DELETE FROM slots WHERE slot_id >= ?").run(maxSlots);
    });
    const insert = this.db.prepare(
      "INSERT OR IGNORE INTO slots(slot_id, health, updated_at) VALUES(?, 'idle', ?)",
    );
    const now = Date.now();
    for (let index = 0; index < maxSlots; index += 1) insert.run(index, now);
  }

  close(): void {
    this.db.close();
  }

  transaction<T>(fn: () => T): T {
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const result = fn();
      this.db.exec("COMMIT");
      return result;
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }

  ensureOwner(owner: OwnerChatKey, unixUidBase = 250_000): void {
    const [kind, externalId] = owner.split(":", 2) as [ChatType, string];
    this.transaction(() => {
      const existing = this.db.prepare("SELECT 1 FROM owners WHERE owner_chat_key = ?").get(owner);
      if (existing) return;
      const row = this.db.prepare("SELECT COALESCE(MAX(unix_uid), ?) AS uid FROM owners").get(unixUidBase - 1) as Row;
      this.db
        .prepare("INSERT INTO owners VALUES(?, ?, ?, ?, ?)")
        .run(owner, kind, externalId, Number(row.uid) + 1, Date.now());
    });
  }

  ownerUnixUid(owner: OwnerChatKey): number {
    const row = this.db.prepare("SELECT unix_uid FROM owners WHERE owner_chat_key = ?").get(owner) as Row | undefined;
    if (!row) throw new Error("owner does not exist");
    return Number(row.unix_uid);
  }

  setAccessRule(direction: "inbound" | "outbound", type: "user" | "group", id: string, decision: "allow" | "deny"): void {
    this.db
      .prepare(`INSERT INTO access_rules VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(direction, subject_type, subject_id)
        DO UPDATE SET decision = excluded.decision, updated_at = excluded.updated_at`)
      .run(direction, type, id, decision, Date.now());
  }

  removeAccessRule(direction: "inbound" | "outbound", type: "user" | "group", id: string): void {
    this.db.prepare("DELETE FROM access_rules WHERE direction = ? AND subject_type = ? AND subject_id = ?").run(direction, type, id);
  }

  accessDecision(direction: "inbound" | "outbound", type: "user" | "group", id: string): "allow" | "deny" | undefined {
    const row = this.db
      .prepare("SELECT decision FROM access_rules WHERE direction = ? AND subject_type = ? AND subject_id = ?")
      .get(direction, type, id) as Row | undefined;
    return row ? (String(row.decision) as "allow" | "deny") : undefined;
  }

  countAccessAllows(direction: "inbound" | "outbound", type: "user" | "group"): number {
    const row = this.db
      .prepare("SELECT COUNT(*) AS count FROM access_rules WHERE direction = ? AND subject_type = ? AND decision = 'allow'")
      .get(direction, type) as Row;
    return Number(row.count);
  }

  accessRules(direction: "inbound" | "outbound", type: "user" | "group", decision: "allow" | "deny"): string[] {
    const rows = this.db
      .prepare("SELECT subject_id FROM access_rules WHERE direction = ? AND subject_type = ? AND decision = ? ORDER BY subject_id")
      .all(direction, type, decision) as Row[];
    return rows.map((row) => String(row.subject_id));
  }

  getSetting(key: string): string | undefined {
    const row = this.db.prepare("SELECT value FROM settings WHERE key = ?").get(key) as Row | undefined;
    return row ? String(row.value) : undefined;
  }

  setSetting(key: string, value: string): void {
    this.db
      .prepare(`INSERT INTO settings VALUES(?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`)
      .run(key, value, Date.now());
  }

  modelSettings(fallback: ModelSettings): ModelSettings {
    return {
      provider: this.getSetting("model.provider") ?? fallback.provider,
      model: this.getSetting("model.id") ?? fallback.model,
      thinkingLevel: (this.getSetting("model.thinking") ?? fallback.thinkingLevel) as ModelSettings["thinkingLevel"],
    };
  }

  insertSession(session: SessionRecord): void {
    this.db
      .prepare(`INSERT INTO sessions(
        owner_chat_key, session_ref, task_id, title, state, branch, base_sha, workspace,
        transcript_path, slot_id, staging_release_id, continuation_of, created_at, updated_at
      ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
      .run(
        session.owner,
        session.sessionRef,
        session.taskId,
        session.title,
        session.state,
        session.branch,
        session.baseSha,
        session.workspace,
        session.transcriptPath,
        session.slotId,
        session.stagingReleaseId,
        session.continuationOf,
        session.createdAt,
        session.updatedAt,
      );
  }

  getSession(owner: OwnerChatKey, sessionRef: string): SessionRecord | undefined {
    const row = this.db
      .prepare("SELECT * FROM sessions WHERE owner_chat_key = ? AND session_ref = ?")
      .get(owner, sessionRef) as Row | undefined;
    return row ? sessionFromRow(row) : undefined;
  }

  listSessions(owner: OwnerChatKey, page: number, pageSize = 8): SessionRecord[] {
    const rows = this.db
      .prepare("SELECT * FROM sessions WHERE owner_chat_key = ? ORDER BY updated_at DESC LIMIT ? OFFSET ?")
      .all(owner, pageSize, (page - 1) * pageSize) as Row[];
    return rows.map(sessionFromRow);
  }

  activeSession(owner: OwnerChatKey): SessionRecord | undefined {
    const row = this.db
      .prepare(`SELECT sessions.* FROM slots JOIN sessions
        ON sessions.owner_chat_key = slots.owner_chat_key AND sessions.session_ref = slots.session_ref
        WHERE slots.owner_chat_key = ?`)
      .get(owner) as Row | undefined;
    return row ? sessionFromRow(row) : undefined;
  }

  updateSession(owner: OwnerChatKey, sessionRef: string, values: Partial<Pick<SessionRecord, "state" | "slotId" | "stagingReleaseId" | "title">>): void {
    const mapping = new Map<string, unknown>([
      ["state", values.state],
      ["slot_id", values.slotId],
      ["staging_release_id", values.stagingReleaseId],
      ["title", values.title],
    ]);
    const entries = [...mapping].filter(([, value]) => value !== undefined);
    if (entries.length === 0) return;
    const assignments = entries.map(([key]) => `${key} = ?`).join(", ");
    const params = entries.map(([, value]) => {
      if (value === undefined) throw new Error("undefined session update");
      return value as SQLInputValue;
    });
    const result = this.db
      .prepare(`UPDATE sessions SET ${assignments}, updated_at = ? WHERE owner_chat_key = ? AND session_ref = ?`)
      .run(...params, Date.now(), owner, sessionRef);
    if (result.changes !== 1) throw new Error("session not found for owner");
  }

  deleteUnstartedSession(owner: OwnerChatKey, sessionRef: string): void {
    this.db
      .prepare("DELETE FROM sessions WHERE owner_chat_key = ? AND session_ref = ? AND state = 'suspended' AND slot_id IS NULL AND staging_release_id IS NULL")
      .run(owner, sessionRef);
  }

  slotForOwner(owner: OwnerChatKey): SlotRecord | undefined {
    const row = this.db.prepare("SELECT * FROM slots WHERE owner_chat_key = ?").get(owner) as Row | undefined;
    return row ? slotFromRow(row) : undefined;
  }

  allocateSlot(owner: OwnerChatKey, sessionRef: string): SlotRecord | undefined {
    return this.transaction(() => {
      const current = this.slotForOwner(owner);
      if (current) {
        this.assignSlot(current.id, owner, sessionRef);
        return this.slot(current.id);
      }
      const idle = this.db.prepare("SELECT * FROM slots WHERE owner_chat_key IS NULL ORDER BY slot_id LIMIT 1").get() as Row | undefined;
      if (!idle) return undefined;
      const id = Number(idle.slot_id);
      this.assignSlot(id, owner, sessionRef);
      return this.slot(id);
    });
  }

  private assignSlot(id: number, owner: OwnerChatKey, sessionRef: string): void {
    this.db
      .prepare("UPDATE sessions SET slot_id = NULL, state = CASE WHEN state = 'active' THEN 'suspended' ELSE state END, updated_at = ? WHERE owner_chat_key = ? AND slot_id = ?")
      .run(Date.now(), owner, id);
    this.db
      .prepare("UPDATE slots SET owner_chat_key = ?, session_ref = ?, health = 'starting', updated_at = ? WHERE slot_id = ?")
      .run(owner, sessionRef, Date.now(), id);
    this.db
      .prepare("UPDATE sessions SET slot_id = ?, state = CASE WHEN state = 'open_pr' THEN state ELSE 'active' END, updated_at = ? WHERE owner_chat_key = ? AND session_ref = ?")
      .run(id, Date.now(), owner, sessionRef);
  }

  releaseSlot(owner: OwnerChatKey, sessionRef: string): void {
    this.transaction(() => {
      this.db
        .prepare("UPDATE slots SET owner_chat_key = NULL, session_ref = NULL, health = 'idle', updated_at = ? WHERE owner_chat_key = ? AND session_ref = ?")
        .run(Date.now(), owner, sessionRef);
      this.db
        .prepare("UPDATE sessions SET slot_id = NULL, state = CASE WHEN state = 'active' THEN 'suspended' ELSE state END, updated_at = ? WHERE owner_chat_key = ? AND session_ref = ?")
        .run(Date.now(), owner, sessionRef);
    });
  }

  setSlotHealth(id: number, health: SlotRecord["health"]): void {
    this.db.prepare("UPDATE slots SET health = ?, updated_at = ? WHERE slot_id = ?").run(health, Date.now(), id);
  }

  slot(id: number): SlotRecord {
    const row = this.db.prepare("SELECT * FROM slots WHERE slot_id = ?").get(id) as Row | undefined;
    if (!row) throw new Error("slot not found");
    return slotFromRow(row);
  }

  slots(): SlotRecord[] {
    return (this.db.prepare("SELECT * FROM slots ORDER BY slot_id").all() as Row[]).map(slotFromRow);
  }

  hasAvailableSlot(owner: OwnerChatKey): boolean {
    if (this.slotForOwner(owner)) return true;
    return Boolean(this.db.prepare("SELECT 1 FROM slots WHERE owner_chat_key IS NULL LIMIT 1").get());
  }

  recordInbound(event: InboundEvent): boolean {
    this.ensureOwner(event.owner);
    return this.transaction(() => {
      this.db.prepare(`INSERT INTO contact_routes VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(owner_chat_key) DO UPDATE SET bot_id = excluded.bot_id,
        chat_type = excluded.chat_type, destination_id = excluded.destination_id,
        updated_at = excluded.updated_at`).run(
        event.owner,
        event.bot_id,
        event.chat_type,
        event.chat_type === "group" ? String(event.group_id) : event.user_id,
        Date.now(),
      );
      const result = this.db
        .prepare("INSERT OR IGNORE INTO inbound_events VALUES(?, ?, ?, ?, ?)")
        .run(event.event_id, event.owner, event.message_id, Date.now(), JSON.stringify(event));
      return result.changes === 1;
    });
  }

  latestContact(owner: OwnerChatKey): { botId: string; chatType: ChatType; destinationId: string } | undefined {
    const row = this.db.prepare("SELECT * FROM contact_routes WHERE owner_chat_key = ?").get(owner) as Row | undefined;
    return row ? {
      botId: String(row.bot_id),
      chatType: String(row.chat_type) as ChatType,
      destinationId: String(row.destination_id),
    } : undefined;
  }

  enqueue(message: OutboxMessage): number {
    this.ensureOwner(message.owner);
    const result = this.db
      .prepare(`INSERT INTO outbox(
        owner_chat_key, session_ref, bot_id, chat_type, destination_id, message_json,
        origin, state, available_at, created_at
      ) VALUES(?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)`)
      .run(
        message.owner,
        message.sessionRef ?? null,
        message.botId,
        message.chatType,
        message.destinationId,
        JSON.stringify(message.message),
        message.origin,
        Date.now(),
        Date.now(),
      );
    return Number(result.lastInsertRowid);
  }

  pollOutbox(botId: string, limit: number): Array<Record<string, unknown>> {
    return this.transaction(() => {
      const rows = this.db
        .prepare(`SELECT * FROM outbox WHERE bot_id = ? AND (
          state = 'pending' OR (state = 'leased' AND leased_at < ?)
        ) AND available_at <= ? ORDER BY outbox_id LIMIT ?`)
        .all(botId, Date.now() - 60_000, Date.now(), Math.min(Math.max(limit, 1), 50)) as Row[];
      const lease = this.db.prepare("UPDATE outbox SET state = 'leased', leased_at = ?, attempts = attempts + 1 WHERE outbox_id = ?");
      for (const row of rows) {
        if (row.outbox_id === undefined) throw new Error("outbox row is missing its id");
        lease.run(Date.now(), row.outbox_id);
      }
      return rows.map((row) => ({
        id: Number(row.outbox_id),
        chat_type: String(row.chat_type),
        destination_id: String(row.destination_id),
        message: JSON.parse(String(row.message_json)),
      }));
    });
  }

  ackOutbox(id: number, botId: string, deliveredMessageId: string): void {
    const row = this.db.prepare("SELECT 1 FROM outbox WHERE outbox_id = ? AND bot_id = ? AND state = 'leased'").get(id, botId) as Row | undefined;
    if (!row) throw new Error("outbox lease not found");
    this.db
      .prepare("UPDATE outbox SET state = 'delivered', delivered_message_id = ? WHERE outbox_id = ? AND bot_id = ?")
      .run(deliveredMessageId, id, botId);
  }

  nackOutbox(id: number, botId: string, error: string): void {
    this.db
      .prepare(`UPDATE outbox SET state = 'pending', last_error = ?, available_at = ?, leased_at = NULL
        WHERE outbox_id = ? AND bot_id = ? AND state = 'leased'`)
      .run(error.slice(0, 500), Date.now() + 5_000, id, botId);
  }

  saveRelease(owner: OwnerChatKey, sessionRef: string, release: { id: string; sha: string; state: string; validation: unknown }): void {
    this.db
      .prepare("INSERT INTO staging_releases VALUES(?, ?, ?, ?, ?, ?, ?)")
      .run(owner, sessionRef, release.id, release.sha, release.state, JSON.stringify(release.validation), Date.now());
  }

  stagingRelease(owner: OwnerChatKey, sessionRef: string, releaseId: string): { id: string; sha: string; state: string } | undefined {
    const row = this.db
      .prepare("SELECT * FROM staging_releases WHERE owner_chat_key = ? AND session_ref = ? AND release_id = ?")
      .get(owner, sessionRef, releaseId) as Row | undefined;
    return row ? {
      id: String(row.release_id),
      sha: String(row.git_sha),
      state: String(row.state),
    } : undefined;
  }

  savePullRequest(owner: OwnerChatKey, sessionRef: string, pr: { number: number; url: string; state: "open" | "merged" | "closed"; headSha: string }): void {
    this.db
      .prepare(`INSERT INTO pull_requests VALUES(?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(owner_chat_key, session_ref) DO UPDATE SET
        number = excluded.number, url = excluded.url, state = excluded.state,
        head_sha = excluded.head_sha, updated_at = excluded.updated_at`)
      .run(owner, sessionRef, pr.number, pr.url, pr.state, pr.headSha, Date.now());
  }

  pullRequest(owner: OwnerChatKey, sessionRef: string): { number: number; url: string; state: "open" | "merged" | "closed"; headSha: string } | undefined {
    const row = this.db
      .prepare("SELECT * FROM pull_requests WHERE owner_chat_key = ? AND session_ref = ?")
      .get(owner, sessionRef) as Row | undefined;
    return row ? {
      number: Number(row.number),
      url: String(row.url),
      state: String(row.state) as "open" | "merged" | "closed",
      headSha: String(row.head_sha),
    } : undefined;
  }

  pullRequests(): Array<{ owner: OwnerChatKey; sessionRef: string; number: number; url: string; state: "open" | "merged" | "closed"; headSha: string }> {
    return (this.db.prepare("SELECT * FROM pull_requests WHERE state = 'open' ORDER BY updated_at").all() as Row[]).map((row) => ({
      owner: String(row.owner_chat_key) as OwnerChatKey,
      sessionRef: String(row.session_ref),
      number: Number(row.number),
      url: String(row.url),
      state: String(row.state) as "open" | "merged" | "closed",
      headSha: String(row.head_sha),
    }));
  }
}
