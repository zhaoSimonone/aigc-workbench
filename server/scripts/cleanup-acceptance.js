// Run inside the tested API container, with a live-test receipt copied into it.
// Only exact, explicitly named acceptance accounts and their COS prefixes qualify.
const fs = require('fs');
const { Pool } = require('pg');
const COS = require('cos-nodejs-sdk-v5');

async function main() {
  const [receiptPath, confirm] = process.argv.slice(2);
  if (!receiptPath || confirm !== '--confirm') throw new Error('Usage: cleanup-acceptance.js receipt.json --confirm');
  const { accounts } = JSON.parse(fs.readFileSync(receiptPath, 'utf8'));
  if (!Array.isArray(accounts) || !accounts.length || accounts.length > 10) throw new Error('Invalid receipt');
  for (const account of accounts) {
    if (!/^[a-f0-9-]{36}$/.test(account.id) || !/^acceptance-[a-f0-9]{32}@example\.invalid$/.test(account.email)) {
      throw new Error('Refusing a non-acceptance account');
    }
  }
  const pool = new Pool({ connectionString: process.env.DATABASE_URL });
  const cos = new COS({ SecretId: process.env.TENCENT_COS_SECRET_ID, SecretKey: process.env.TENCENT_COS_SECRET_KEY });
  const base = { Bucket: process.env.TENCENT_COS_BUCKET, Region: process.env.TENCENT_COS_REGION || 'ap-guangzhou' };
  const call = (method, options) => new Promise((resolve, reject) =>
    cos[method]({ ...base, ...options }, (error, result) => error ? reject(error) : resolve(result)));
  try {
    // Validate every account before the first deletion.
    for (const account of accounts) {
      const { rows } = await pool.query('SELECT email FROM users WHERE id=$1', [account.id]);
      if (rows.length !== 1 || rows[0].email !== account.email) throw new Error('Account identity mismatch');
    }
    let removed = 0;
    for (const account of accounts) {
      const prefix = `${account.id}/`;
      for (;;) {
        const listed = await call('getBucket', { Prefix: prefix, MaxKeys: 1000 });
        const objects = listed.Contents || [];
        if (!objects.length) break;
        for (const object of objects) {
          if (!object.Key.startsWith(prefix)) throw new Error('Unexpected COS object prefix');
          await call('deleteObject', { Key: object.Key });
          removed++;
        }
      }
      await pool.query('DELETE FROM users WHERE id=$1 AND email=$2', [account.id, account.email]);
      const { rows } = await pool.query('SELECT count(*)::int AS count FROM assets WHERE user_id=$1', [account.id]);
      if (rows[0].count !== 0) throw new Error('Test assets remain');
    }
    console.log(JSON.stringify({ cleanedAccounts: accounts.length, deletedTestObjects: removed }));
  } finally {
    await pool.end();
  }
}

main().catch(() => { console.error('Acceptance cleanup failed; inspect the scoped receipt and server state'); process.exitCode = 1; });
