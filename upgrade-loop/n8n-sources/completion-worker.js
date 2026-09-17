'use strict';
// Runs inside n8n. Reads execution state only; never invokes a workflow or touches the host.
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');
const f = require('./completion-feedback');
const dependency = name => require(require.resolve(name, { paths: ['/usr/local/lib/node_modules/n8n'] }));
fs.mkdirSync(f.ROOT, { recursive: true, mode: 0o700 });
const socket = path.join(f.ROOT, 'worker.sock');
const lock = net.createServer(connection => connection.end());
let attemptedRecovery = false;
lock.on('error', error => {
  if (error.code !== 'EADDRINUSE' || attemptedRecovery) process.exit(1);
  const probe = net.connect(socket, () => process.exit(0));
  probe.on('error', error => {
    if (error.code !== 'ECONNREFUSED') process.exit(1);
    attemptedRecovery = true;
    fs.unlinkSync(socket); lock.listen(socket);
  });
});
lock.on('listening', async () => {
  fs.chmodSync(socket, 0o600);
  let read;
  if (process.env.DB_TYPE === 'postgresdb') {
    const { Client } = dependency('pg');
    const db = new Client({ host: process.env.DB_POSTGRESDB_HOST, port: Number(process.env.DB_POSTGRESDB_PORT || 5432),
      database: process.env.DB_POSTGRESDB_DATABASE, user: process.env.DB_POSTGRESDB_USER, password: process.env.DB_POSTGRESDB_PASSWORD });
    db.on('error', () => process.exit(1));
    await db.connect();
    await db.query('SET default_transaction_read_only = on');
    read = async id => (await db.query('SELECT status, "workflowId" FROM execution_entity WHERE id = $1', [id])).rows[0];
  } else {
    const sqlite = dependency('sqlite3');
    const db = new sqlite.Database(process.env.FEDORA45_FEEDBACK_DATABASE || '/home/node/.n8n/database.sqlite', sqlite.OPEN_READONLY);
    read = id => new Promise((resolve, reject) => db.get('SELECT status, workflowId FROM execution_entity WHERE id = ?', [id], (err, row) => err ? reject(err) : resolve(row)));
  }
  async function cycle() {
    try { await f.tick(read); fs.writeFileSync(path.join(f.ROOT, 'heartbeat'), String(Date.now()), { mode: 0o600 }); }
    catch { /* Retry after temporary DB or filesystem failures; never log a secret. */ }
    setTimeout(cycle, 3000);
  }
  cycle();
});
lock.listen(socket);
