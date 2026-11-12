#!/usr/bin/env node
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const USER_AGENT = process.env.SEC_USER_AGENT;
if (!USER_AGENT) {
  console.error('SEC_USER_AGENT is required. Format: "Name email@example.com"');
  process.exit(1);
}

const COMPANIES = [
  { name: 'Apple Inc.', ticker: 'AAPL', cik: '0000320193' },
  { name: 'Microsoft Corporation', ticker: 'MSFT', cik: '0000789019' },
  { name: 'NVIDIA Corporation', ticker: 'NVDA', cik: '0001045810' },
  { name: 'Tesla, Inc.', ticker: 'TSLA', cik: '0001318605' },
  { name: 'Amazon.com, Inc.', ticker: 'AMZN', cik: '0001018724' },
];

const METRIC_TAGS = {
  'Revenue': ['Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax', 'SalesRevenueNet'],
  'Net Income': ['NetIncomeLoss'],
  'Total Assets': ['Assets'],
  'Total Liabilities': ['Liabilities'],
  'Shareholders\' Equity': ['StockholdersEquity', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'],
  'Operating Cash Flow': ['NetCashProvidedByUsedInOperatingActivities'],
  'Cash and Cash Equivalents': ['CashAndCashEquivalentsAtCarryingValue'],
  'R&D Expense': ['ResearchAndDevelopmentExpense'],
};

const DURATION_METRICS = new Set(['Revenue', 'Net Income', 'Operating Cash Flow', 'R&D Expense']);
const INSTANT_METRICS = new Set(['Total Assets', 'Total Liabilities', 'Shareholders\' Equity', 'Cash and Cash Equivalents']);

const lastRequest = { time: 0 };
async function throttle() {
  const now = Date.now();
  const elapsed = now - lastRequest.time;
  const minInterval = 200;
  if (elapsed < minInterval) {
    await new Promise(r => setTimeout(r, minInterval - elapsed));
  }
  lastRequest.time = Date.now();
}

async function fetchJSON(url) {
  await throttle();
  const res = await fetch(url, {
    headers: {
      'User-Agent': USER_AGENT,
      'Accept-Encoding': 'gzip, deflate',
    },
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch ${url}: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

function getLatest10K(usGaapFacts) {
  const candidates = [];
  for (const [tag, fact] of Object.entries(usGaapFacts)) {
    const units = fact.units || {};
    for (const [unitKey, entries] of Object.entries(units)) {
      if (unitKey !== 'USD') continue;
      for (const entry of entries) {
        if (entry.form === '10-K' && entry.fp === 'FY') {
          candidates.push({ ...entry, tag });
        }
      }
    }
  }
  if (candidates.length === 0) return null;
  const byAccn = new Map();
  for (const c of candidates) {
    const accn = c.accn;
    if (!byAccn.has(accn)) {
      byAccn.set(accn, { accn, filed: c.filed, fy: c.fy, end: c.end, entries: [] });
    }
    byAccn.get(accn).entries.push(c);
  }
  const accns = Array.from(byAccn.values());
  accns.sort((a, b) => (b.filed || '').localeCompare(a.filed || '') || (b.end || '').localeCompare(a.end || ''));
  return accns[0];
}

function pickEntry(entries, tag, metricName, accn) {
  const relevant = entries.filter(e => e.accn === accn && e.tag === tag && e.form === '10-K' && e.fp === 'FY');
  if (relevant.length === 0) return null;
  
  if (DURATION_METRICS.has(metricName)) {
    const duration = relevant.filter(e => e.start && e.end);
    if (duration.length > 0) {
      duration.sort((a, b) => {
        const da = Math.abs((new Date(b.end) - new Date(b.start)) / (1000*60*60*24) - 365);
        const db = Math.abs((new Date(a.end) - new Date(a.start)) / (1000*60*60*24) - 365);
        return da - db;
      });
      return duration[0];
    }
  } else if (INSTANT_METRICS.has(metricName)) {
    const instant = relevant.filter(e => !e.start && e.end);
    if (instant.length > 0) {
      return instant[0];
    }
  }
  return relevant[0];
}

function collectEntriesForTag(usGaap, tag) {
  const entries = [];
  if (!usGaap[tag]) return entries;
  for (const [unitKey, unitEntries] of Object.entries(usGaap[tag].units || {})) {
    if (unitKey !== 'USD') continue;
    for (const e of unitEntries) {
      entries.push({ ...e, tag });
    }
  }
  return entries;
}

async function main() {
  const result = {
    retrieved_at: new Date().toISOString(),
    companies: {}
  };
  
  for (const company of COMPANIES) {
    console.log(`Fetching ${company.ticker}...`);
    const cikPadded = company.cik.padStart(10, '0');
    const facts = await fetchJSON(`https://data.sec.gov/api/xbrl/companyfacts/CIK${cikPadded}.json`);
    const usGaap = facts.facts?.['us-gaap'] || {};
    
    const latest10K = getLatest10K(usGaap);
    if (!latest10K) {
      throw new Error(`No 10-K found for ${company.ticker}`);
    }
    
    const accn = latest10K.accn;
    const fiscalYear = latest10K.fy;
    const periodEnd = latest10K.end;
    const filed = latest10K.filed;
    const cikNoLeadingZeros = String(parseInt(company.cik));
    const accnNoDashes = accn.replace(/-/g, '');
    const filingIndexUrl = `https://www.sec.gov/Archives/edgar/data/${cikNoLeadingZeros}/${accnNoDashes}/`;
    
    const metrics = {};
    const report = {
      tagUsed: {},
      derived: {},
      missing: []
    };
    
    for (const [metricName, candidates] of Object.entries(METRIC_TAGS)) {
      if (metricName === 'R&D Expense') {
        let found = false;
        for (const tag of candidates) {
          const entries = collectEntriesForTag(usGaap, tag);
          const entry = pickEntry(entries, tag, metricName, accn);
          if (entry && entry.val !== undefined) {
            const allFY = entries.filter(e => e.accn === accn && e.form === '10-K' && e.fp === 'FY');
            allFY.sort((a,b) => (b.end||'').localeCompare(a.end||''));
            const prior = allFY[1];
            metrics[metricName] = {
              value_usd: Math.round(entry.val),
              prior_year_value_usd: prior && prior.val !== undefined ? Math.round(prior.val) : null,
              xbrl_tag: tag,
              derived: false,
              period_start: entry.start || null,
              period_end: entry.end || periodEnd,
            };
            report.tagUsed[metricName] = tag;
            report.derived[metricName] = false;
            found = true;
            break;
          }
        }
        if (!found && usGaap['OperatingIncomeLoss']) {
          const entries = collectEntriesForTag(usGaap, 'OperatingIncomeLoss');
          const entry = pickEntry(entries, 'OperatingIncomeLoss', metricName, accn);
          if (entry && entry.val !== undefined) {
            const allFY = entries.filter(e => e.accn === accn && e.form === '10-K' && e.fp === 'FY');
            allFY.sort((a,b) => (b.end||'').localeCompare(a.end||''));
            const prior = allFY[1];
            metrics['R&D Expense'] = {
              value_usd: Math.round(entry.val),
              prior_year_value_usd: prior && prior.val !== undefined ? Math.round(prior.val) : null,
              xbrl_tag: 'OperatingIncomeLoss',
              derived: false,
              period_start: entry.start || null,
              period_end: entry.end || periodEnd,
              relabeled_from: 'OperatingIncomeLoss'
            };
            report.tagUsed['R&D Expense'] = 'OperatingIncomeLoss (relabelled)';
            report.derived['R&D Expense'] = false;
            found = true;
          }
        }
        if (!found) report.missing.push(metricName);
        continue;
      }
      
      let found = false;
      for (const tag of candidates) {
        const entries = collectEntriesForTag(usGaap, tag);
        const entry = pickEntry(entries, tag, metricName, accn);
        if (entry && entry.val !== undefined) {
          const allFY = entries.filter(e => e.accn === accn && e.form === '10-K' && e.fp === 'FY');
          allFY.sort((a,b) => (b.end||'').localeCompare(a.end||''));
          const prior = allFY[1];
          metrics[metricName] = {
            value_usd: Math.round(entry.val),
            prior_year_value_usd: prior && prior.val !== undefined ? Math.round(prior.val) : null,
            xbrl_tag: tag,
            derived: false,
            period_start: entry.start || null,
            period_end: entry.end || periodEnd,
          };
          report.tagUsed[metricName] = tag;
          report.derived[metricName] = false;
          found = true;
          break;
        }
      }
      if (!found && metricName === 'Total Liabilities') {
        if (metrics['Total Assets'] && usGaap['StockholdersEquity']) {
          const eqEntries = collectEntriesForTag(usGaap, 'StockholdersEquity');
          const eqEntry = pickEntry(eqEntries, 'StockholdersEquity', 'Shareholders\' Equity', accn);
          if (eqEntry && eqEntry.val !== undefined) {
            const derivedVal = metrics['Total Assets'].value_usd - Math.round(eqEntry.val);
            const allFY = eqEntries.filter(e => e.accn === accn && e.form === '10-K' && e.fp === 'FY');
            allFY.sort((a,b) => (b.end||'').localeCompare(a.end||''));
            const priorEq = allFY[1];
            const priorAssets = metrics['Total Assets'].prior_year_value_usd;
            const priorDerived = (priorAssets && priorEq && priorEq.val !== undefined) ? (priorAssets - Math.round(priorEq.val)) : null;
            metrics['Total Liabilities'] = {
              value_usd: derivedVal,
              prior_year_value_usd: priorDerived,
              xbrl_tag: 'LiabilitiesAndStockholdersEquity - StockholdersEquity',
              derived: true,
              period_start: metrics['Total Assets'].period_start,
              period_end: metrics['Total Assets'].period_end,
            };
            report.tagUsed['Total Liabilities'] = 'Derived (LiabilitiesAndStockholdersEquity - StockholdersEquity)';
            report.derived['Total Liabilities'] = true;
            found = true;
          }
        }
        // Try alternative tag names for equity
        if (!found && metrics['Total Assets']) {
          for (const eqTag of ['StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest']) {
            if (usGaap[eqTag]) {
              const eqEntries = collectEntriesForTag(usGaap, eqTag);
              const eqEntry = pickEntry(eqEntries, eqTag, 'Shareholders\' Equity', accn);
              if (eqEntry && eqEntry.val !== undefined) {
                const derivedVal = metrics['Total Assets'].value_usd - Math.round(eqEntry.val);
                const allFY = eqEntries.filter(e => e.accn === accn && e.form === '10-K' && e.fp === 'FY');
                allFY.sort((a,b) => (b.end||'').localeCompare(a.end||''));
                const priorEq = allFY[1];
                const priorAssets = metrics['Total Assets'].prior_year_value_usd;
                const priorDerived = (priorAssets && priorEq && priorEq.val !== undefined) ? (priorAssets - Math.round(priorEq.val)) : null;
                metrics['Total Liabilities'] = {
                  value_usd: derivedVal,
                  prior_year_value_usd: priorDerived,
                  xbrl_tag: 'LiabilitiesAndStockholdersEquity - ' + eqTag,
                  derived: true,
                  period_start: metrics['Total Assets'].period_start,
                  period_end: metrics['Total Assets'].period_end,
                };
                report.tagUsed['Total Liabilities'] = 'Derived (' + eqTag + ')';
                report.derived['Total Liabilities'] = true;
                found = true;
                break;
              }
            }
          }
        }
      }
      if (!found) report.missing.push(metricName);
    }
    
    result.companies[company.ticker] = {
      name: company.name,
      ticker: company.ticker,
      cik: company.cik,
      fiscalYearEnd: company.ticker === 'MSFT' ? '06-30' : 
                      company.ticker === 'AAPL' ? '09-27' : 
                      company.ticker === 'NVDA' ? '01-26' : 
                      (company.ticker === 'TSLA' || company.ticker === 'AMZN') ? '12-31' : periodEnd.slice(5),
      fiscal_year: fiscalYear,
      period_end: periodEnd,
      accession_number: accn,
      filed_date: filed,
      filing_index_url: filingIndexUrl,
      metrics,
      report
    };
    
    console.log(`Done ${company.ticker}:`, report.tagUsed, 'missing:', report.missing);
  }
  
  const outPath = path.join(__dirname, '..', 'frontend', 'src', 'api', 'mock', 'fixtures', 'sec-snapshot.json');
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, JSON.stringify(result, null, 2));
  console.log(`\nWrote ${outPath}`);
}

main().catch(e => {
  console.error('Error:', e);
  process.exit(1);
});
