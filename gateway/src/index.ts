import { serve } from "@hono/node-server";
import { Octokit } from "@octokit/rest";
import { createApp } from "./app.js";
import { loadConfig } from "./config.js";
import { HttpCoreClient } from "./core-client.js";
import { GitHubNotifier } from "./notifier.js";

const config = loadConfig(process.env);
const app = createApp({
  config,
  core: new HttpCoreClient(config.coreUrl, config.coreApiToken),
  notifier: new GitHubNotifier(new Octokit({ auth: config.githubToken })),
});

serve({ fetch: app.fetch, port: config.port }, ({ port }) => {
  console.log(`gateway listening on :${port}`);
});
