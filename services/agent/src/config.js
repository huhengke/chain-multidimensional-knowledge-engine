import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readFileSync } from 'node:fs';
const agentRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const workspaceRoot = resolve(agentRoot, '../..');
const installationRoot = process.env.PLATFORM_INSTALLATION_DIR || join(workspaceRoot, '.runtime/standalone');
const data = join(installationRoot, 'agent');
const secrets = JSON.parse(readFileSync(join(installationRoot, 'installation.json'), 'utf8'));
export const config = Object.freeze({
  agentRoot, workspaceRoot, installationRoot,
  port: Number(process.env.AGENT_PORT || 18001), bindHost: '127.0.0.1',
  engineBaseUrl: process.env.ENGINE_BASE_URL || 'http://127.0.0.1:18000',
  platformBaseUrl: process.env.PLATFORM_BASE_URL || 'http://127.0.0.1:8079',
  serviceToken: secrets.service_token,
  runTimeoutMs: Number(process.env.AGENT_RUN_TIMEOUT_MS || 180000), maxRequestBytes: 65536,
  sessionIndexPath: join(data, 'sessions.json'), queryLogPath: join(data, 'queries.jsonl'),
  artifactLogPath: join(data, 'artifacts.jsonl'), attachmentRoot: join(data, 'attachments'),
  skillRoot: join(data, 'skills'), disabledSkillRoot: join(data, 'skills-disabled'),
  skillArchiveRoot: join(data, 'skill-archive'), skillStagingRoot: join(data, 'skill-staging'),
  skillRuntimeRoot: join(data, 'runtime/skills'),
});
export function harnessEnvironment(overrides = {}) {
  return { ...process.env, ...overrides, ENGINE_BASE_URL: config.engineBaseUrl,
    ENGINE_SERVICE_TOKEN: config.serviceToken, CHAIN_AGENT_ATTACHMENT_ROOT: config.attachmentRoot,
    CHAIN_AGENT_QUERY_LOG: config.queryLogPath, CHAIN_AGENT_ARTIFACT_LOG: config.artifactLogPath };
}
