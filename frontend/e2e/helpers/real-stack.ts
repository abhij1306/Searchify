/**
 * Real-stack lifecycle helper for `content-integration.spec.ts` (Task 8).
 *
 * Boots the WHOLE vertical with no stubs in app code:
 *   - a disposable Postgres database `searchify_e2e_<runid>` created from the
 *     admin server behind `E2E_ADMIN_DATABASE_URL` (fallback: the backend
 *     `settings.database_url` server), schema from `Base.metadata` — never
 *     alembic;
 *   - an in-process mock Mistral server answering the real
 *     `POST /v1/chat/completions` shape (the swap is purely
 *     `CONTENT_PROVIDER_ENDPOINT`; the real connector + parsing run end to
 *     end);
 *   - the FastAPI app (uvicorn), the content worker
 *     (`python -m app.workers.content_worker`) and the Next.js dev server,
 *     all sharing the disposable `DATABASE_URL`.
 *
 * The mock server runs inside the Playwright worker process (the spec's
 * `beforeAll`), so the spec inspects `mockProvider.requests` directly — no
 * control endpoints needed. Everything is torn down (processes killed, DB
 * dropped) in `stop()`, which the spec calls from `afterAll`.
 */
import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import crypto from 'node:crypto';
import path from 'node:path';

export const API_PORT = 8177;
export const MOCK_PORT = 8178;
export const FRONTEND_PORT = 3177;
export const API_ORIGIN = `http://127.0.0.1:${API_PORT}`;
export const MOCK_ORIGIN = `http://127.0.0.1:${MOCK_PORT}`;
export const FRONTEND_ORIGIN = `http://127.0.0.1:${FRONTEND_PORT}`;

export const MOCK_API_KEY = 'dummy-e2e-key';
export const MOCK_RETURNED_MODEL = 'mistral-small-2506';
/** Deterministic output with hostile bits so sanitisation is proven end to end. */
export const MOCK_MARKDOWN = [
  '# Acme Launch Page',
  '',
  'Welcome to **Acme** — we make excellent things.',
  '',
  '- Fast to deploy',
  '- Easy to love',
  '',
  '<script>window.pwned = true;</script>',
  '',
  '[Contact us](https://acme.example/contact) · [Bad link](javascript:alert(1))',
  '',
].join('\n');

interface RecordedProviderRequest {
  authorization: string | null;
  model: unknown;
  messageCount: number;
}

interface MockProviderState {
  requests: RecordedProviderRequest[];
  /** Delay (ms) applied to every provider response — used by the Cancel test. */
  delayMs: number;
  /** One-shot HTTP status override — used to produce a terminal failure. */
  failNextWithStatus: number | null;
}

export const mockProvider: MockProviderState = {
  requests: [],
  delayMs: 0,
  failNextWithStatus: null,
};

export function setProviderDelay(ms: number): void {
  mockProvider.delayMs = ms;
}

export function failNextProviderCallWith(status: number): void {
  mockProvider.failNextWithStatus = status;
}

export interface RealStack {
  dbName: string;
  appDatabaseUrl: string;
  stop(): Promise<void>;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function findRepoRoot(): string {
  let dir = process.cwd();
  for (let i = 0; i < 6; i += 1) {
    if (fs.existsSync(path.join(dir, 'backend')) && fs.existsSync(path.join(dir, 'frontend'))) {
      return dir;
    }
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error('real-stack: could not locate the repo root (run Playwright from frontend/)');
}

// --- Disposable database (admin ops run through the backend's own Python
// env: asyncpg + sqlalchemy are already there, so the frontend needs no
// Postgres driver dependency). ---

const DB_ADMIN_SCRIPT = `
import asyncio, os, sys
import asyncpg
from sqlalchemy.engine import make_url

action, db_name = sys.argv[1], sys.argv[2]
base = os.environ.get("E2E_ADMIN_DATABASE_URL") or ""
if not base:
    from app.core.config import settings
    base = settings.database_url
url = make_url(base)
admin_dsn = url.set(drivername="postgresql", database="postgres").render_as_string(
    hide_password=False
)

async def main() -> None:
    conn = await asyncpg.connect(dsn=admin_dsn)
    try:
        if action == "create":
            await conn.execute(f'CREATE DATABASE "{db_name}"')
        else:
            await conn.execute(
                f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'
            )
    finally:
        await conn.close()

asyncio.run(main())
print(url.set(drivername="postgresql+asyncpg", database=db_name).render_as_string(hide_password=False))
`;

// Greenfield policy: schema comes from Base.metadata, never alembic.
// Importing app.main guarantees every model module is registered first.
const SCHEMA_CREATE_SCRIPT = `
import asyncio, os
from sqlalchemy.ext.asyncio import create_async_engine
import app.main  # noqa: F401
from app.core.database import Base

async def main() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

asyncio.run(main())
`;

function runPython(
  backendDir: string,
  script: string,
  args: string[],
  env: NodeJS.ProcessEnv,
): string {
  const result = spawnSync('uv', ['run', 'python', '-c', script, ...args], {
    cwd: backendDir,
    env,
    encoding: 'utf8',
    timeout: 120_000,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`real-stack python step failed (exit ${result.status}):\n${result.stderr}`);
  }
  return result.stdout.trim();
}

// --- Child process management ---

interface ManagedProcess {
  name: string;
  child: ChildProcess;
  logTail: string[];
}

function launch(
  name: string,
  command: string,
  args: string[],
  options: { cwd: string; env: NodeJS.ProcessEnv },
): ManagedProcess {
  const child = spawn(command, args, {
    cwd: options.cwd,
    env: options.env,
    detached: process.platform !== 'win32',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const logTail: string[] = [];
  const capture = (chunk: Buffer) => {
    for (const line of chunk.toString('utf8').split(/\r?\n/)) {
      if (line.trim() === '') continue;
      logTail.push(line);
      if (logTail.length > 120) logTail.shift();
    }
  };
  child.stdout?.on('data', capture);
  child.stderr?.on('data', capture);
  return { name, child, logTail };
}

function killTree(proc: ManagedProcess): void {
  const { child } = proc;
  if (child.pid === undefined || child.exitCode !== null) return;
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/pid', String(child.pid), '/T', '/F'], { encoding: 'utf8' });
  } else {
    try {
      process.kill(-child.pid, 'SIGKILL');
    } catch {
      child.kill('SIGKILL');
    }
  }
}

async function waitForHttp(
  url: string,
  timeoutMs: number,
  label: string,
  procs: ManagedProcess[],
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastError = 'no attempt made';
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { redirect: 'manual' });
      if (response.status < 500) return;
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = String(error);
    }
    const dead = procs.find((p) => p.child.exitCode !== null);
    if (dead) {
      throw new Error(
        `real-stack: ${dead.name} exited (code ${dead.child.exitCode}) while waiting for ${label}.\n` +
          `--- ${dead.name} log tail ---\n${dead.logTail.join('\n')}`,
      );
    }
    await sleep(500);
  }
  const tails = procs.map((p) => `--- ${p.name} log tail ---\n${p.logTail.join('\n')}`).join('\n');
  throw new Error(`real-stack: ${label} not ready after ${timeoutMs}ms (${lastError})\n${tails}`);
}

// --- Mock Mistral server ---

function startMockProvider(): Promise<http.Server> {
  const server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', () => {
      void (async () => {
        if (req.method === 'POST' && req.url === '/v1/chat/completions') {
          let parsedBody: { model?: unknown; messages?: unknown[] } = {};
          try {
            parsedBody = JSON.parse(Buffer.concat(chunks).toString('utf8'));
          } catch {
            // keep defaults; the recorder still captures the auth header
          }
          mockProvider.requests.push({
            authorization: req.headers.authorization ?? null,
            model: parsedBody.model,
            messageCount: Array.isArray(parsedBody.messages) ? parsedBody.messages.length : 0,
          });
          if (mockProvider.delayMs > 0) await sleep(mockProvider.delayMs);
          // The cancel test aborts mid-delay: writing to a dead socket after
          // the sleep throws ECONNRESET — bail out if the client is gone.
          if (req.destroyed || res.writableEnded || res.socket?.destroyed) return;
          if (mockProvider.failNextWithStatus !== null) {
            const status = mockProvider.failNextWithStatus;
            mockProvider.failNextWithStatus = null;
            res.writeHead(status, { 'content-type': 'application/json' });
            res.end(JSON.stringify({ message: 'injected e2e failure' }));
            return;
          }
          res.writeHead(200, { 'content-type': 'application/json' });
          res.end(
            JSON.stringify({
              id: 'cmpl-e2e',
              object: 'chat.completion',
              model: MOCK_RETURNED_MODEL,
              choices: [
                {
                  index: 0,
                  message: { role: 'assistant', content: MOCK_MARKDOWN },
                  finish_reason: 'stop',
                },
              ],
              usage: { prompt_tokens: 12, completion_tokens: 34, total_tokens: 46 },
            }),
          );
          return;
        }
        // Readiness probe / unknown route.
        res.writeHead(req.method === 'GET' ? 200 : 404, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ ok: true }));
      })();
    });
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(MOCK_PORT, '127.0.0.1', () => resolve(server));
  });
}

// --- Lifecycle ---

export async function startRealStack(): Promise<RealStack> {
  const repoRoot = findRepoRoot();
  const backendDir = path.join(repoRoot, 'backend');
  const frontendDir = path.join(repoRoot, 'frontend');
  const runId = crypto.randomUUID().replace(/-/g, '').slice(0, 12);
  const dbName = `searchify_e2e_${runId}`;

  mockProvider.requests = [];
  mockProvider.delayMs = 0;
  mockProvider.failNextWithStatus = null;

  const procs: ManagedProcess[] = [];
  let mockServer: http.Server | null = null;
  let dbCreated = false;

  const stop = async (): Promise<void> => {
    for (const proc of procs) killTree(proc);
    if (mockServer) {
      await new Promise<void>((resolve) => mockServer?.close(() => resolve()));
      mockServer = null;
    }
    if (dbCreated) {
      runPython(backendDir, DB_ADMIN_SCRIPT, ['drop', dbName], process.env);
      dbCreated = false;
    }
  };

  try {
    // 1. Disposable DB + greenfield schema.
    const appDatabaseUrl = runPython(backendDir, DB_ADMIN_SCRIPT, ['create', dbName], process.env)
      .split(/\r?\n/)
      .at(-1)!
      .trim();
    dbCreated = true;
    runPython(backendDir, SCHEMA_CREATE_SCRIPT, [], {
      ...process.env,
      DATABASE_URL: appDatabaseUrl,
    });

    // 2. Mock provider (in this process, so the spec can read mockProvider).
    mockServer = await startMockProvider();

    // 3. API + content worker + frontend, all against the disposable DB. The
    //    endpoint swap is env-only: the real Mistral connector runs unmodified.
    const backendEnv: NodeJS.ProcessEnv = {
      ...process.env,
      DATABASE_URL: appDatabaseUrl,
      APP_ENV: 'development',
      CONTENT_PROVIDER: 'mistral',
      CONTENT_MODEL: 'mistral-small-latest',
      MISTRAL_API_KEY: MOCK_API_KEY,
      CONTENT_PROVIDER_ENDPOINT: `${MOCK_ORIGIN}/v1/chat/completions`,
      // Speed the queue up so the spec is not dominated by poll intervals.
      CONTENT_POLL_INTERVAL_SECONDS: '0.2',
      CONTENT_HEARTBEAT_INTERVAL_SECONDS: '5',
      CONTENT_RETRY_BASE_DELAY_SECONDS: '0.2',
      CONTENT_RETRY_MAX_DELAY_SECONDS: '1',
    };
    procs.push(
      launch(
        'api',
        'uv',
        ['run', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(API_PORT)],
        { cwd: backendDir, env: backendEnv },
      ),
      launch('content-worker', 'uv', ['run', 'python', '-m', 'app.workers.content_worker'], {
        cwd: backendDir,
        env: backendEnv,
      }),
      launch(
        'frontend',
        process.execPath,
        [
          path.join(frontendDir, 'node_modules', 'next', 'dist', 'bin', 'next'),
          'dev',
          '-p',
          String(FRONTEND_PORT),
        ],
        { cwd: frontendDir, env: { ...process.env, BACKEND_ORIGIN: API_ORIGIN } },
      ),
    );

    // 4. Readiness: every HTTP surface answers before the spec starts.
    await waitForHttp(`${MOCK_ORIGIN}/`, 10_000, 'mock provider', procs);
    await waitForHttp(`${API_ORIGIN}/health`, 120_000, 'API', procs);
    await waitForHttp(`${FRONTEND_ORIGIN}/`, 180_000, 'frontend', procs);

    return { dbName, appDatabaseUrl, stop };
  } catch (error) {
    await stop();
    throw error;
  }
}
