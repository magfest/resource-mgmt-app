// Polls the app for label jobs and sends each to its named printer through
// Zebra Browser Print on this computer. Every call has a timeout. All text is set with textContent
// because job text comes from people.
(function () {
  'use strict';
  var body = document.getElementById('station');
  var COLLECT_URL = body.dataset.collectUrl;
  var REPORT_URL = body.dataset.reportUrl;
  var AGENT = 'http://localhost:9100';
  var NO_AGENT = 'Browser Print is not running or has not accepted this site.';
  var NO_APP = 'Cannot reach the app. Retrying.';
  var APP_TIMEOUT = 10000;
  var AVAILABLE_TIMEOUT = 5000;
  var WRITE_TIMEOUT = 15000;
  var AFTER_FAILURE = 'Not sent: this printer did not take the previous label.';
  var busy = false;          // covers the collect call only
  var agentRead = null;      // the list read in progress, if any
  var agentDevices = null;   // latest /available list; null when the last read failed
  var agentNames = [];
  var agentProblem = '';
  var lastPrinters = [];
  var results = {};  // device_name -> {text, bad} from this page's own sends
  var chains = {};   // printer id -> promise of that printer's sends
  var inFlight = {}; // printer id -> number of batches still sending

  // fetch with a deadline; the promise rejects when the timer aborts it.
  function fetchWithin(url, options, ms) {
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, ms);
    options.signal = controller.signal;
    return fetch(url, options).then(function (r) {
      clearTimeout(timer);
      return r;
    }, function (err) {
      clearTimeout(timer);
      throw err;
    });
  }

  function post(url, payload) {
    return fetchWithin(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }, APP_TIMEOUT).then(function (r) {
      return r.json().then(function (data) { return data; }, function () {
        return { ok: false, message: NO_APP };
      });
    });
  }

  function available() {
    return fetchWithin(AGENT + '/available', {}, AVAILABLE_TIMEOUT).then(function (r) {
      if (!r.ok) { throw new Error('available'); }
      return r.json();
    }).then(function (data) { return data.printer || []; });
  }

  // Reads the printer list whether or not jobs wait, so an idle station shows a dead Browser Print.
  function refreshAgent() {
    if (agentRead) { return agentRead; }
    agentRead = available().then(function (devices) {
      agentDevices = devices;
      agentNames = devices.map(function (d) { return d.name; });
      agentProblem = '';
    }, function () {
      agentDevices = null;
      agentNames = [];
      agentProblem = NO_AGENT;
    }).then(function () {
      agentRead = null;
      draw();
    });
    return agentRead;
  }

  function writeLabel(device, zpl) {
    // Browser Print wants text/plain with a JSON string body.
    return fetchWithin(AGENT + '/write', {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: JSON.stringify({ device: device, data: zpl })
    }, WRITE_TIMEOUT).then(function (r) {
      if (!r.ok) { throw new Error('write'); }
    });
  }

  function report(id, ok, error) {
    return post(REPORT_URL, { job_id: id, ok: ok, error: error || '' })
      .catch(function () { /* the app fails the job after 60 seconds */ });
  }

  function fail(job, msg) {
    results[job.device_name] = { text: msg, bad: true };
    return report(job.id, false, msg);
  }

  // Resolves true when the printer took the label.
  function sendJob(job) {
    var device = null;
    var devices = agentDevices || [];
    for (var i = 0; i < devices.length; i++) {
      if (devices[i].name === job.device_name) { device = devices[i]; break; }
    }
    if (!agentDevices) {
      return fail(job, NO_AGENT).then(function () { return false; });
    }
    if (!device) {
      return fail(job, 'Browser Print has no printer named "' + job.device_name + '".')
        .then(function () { return false; });
    }
    return writeLabel(device, job.zpl).then(function () {
      results[job.device_name] = { text: 'Sent a label.', bad: false };
      return report(job.id, true).then(function () { return true; });
    }, function () {
      return fail(job, 'Browser Print refused the label.').then(function () { return false; });
    });
  }

  // One printer's jobs run in order, and stop at the first failed write.
  function sendGroup(jobs) {
    var stopped = false;
    return jobs.reduce(function (chain, job) {
      return chain.then(function () {
        if (stopped) { return fail(job, AFTER_FAILURE); }
        return sendJob(job).then(function (ok) { if (!ok) { stopped = true; } });
      });
    }, Promise.resolve());
  }

  function startGroup(printerId, jobs) {
    inFlight[printerId] = (inFlight[printerId] || 0) + 1;
    var run = function () {
      return (agentDevices ? Promise.resolve() : refreshAgent()).then(function () {
        return sendGroup(jobs);
      });
    };
    chains[printerId] = (chains[printerId] || Promise.resolve()).then(run).catch(function () {
      /* each job reports its own result; the app fails any left unreported */
    }).then(function () {
      inFlight[printerId] -= 1;
      draw();
    });
  }

  function startAll(jobs) {
    var groups = {};
    jobs.forEach(function (job) {
      (groups[job.printer_id] = groups[job.printer_id] || []).push(job);
    });
    Object.keys(groups).forEach(function (id) { startGroup(id, groups[id]); });
  }

  function sendingIds() {
    return Object.keys(inFlight).filter(function (id) { return inFlight[id] > 0; })
      .map(function (id) { return parseInt(id, 10); });
  }

  function line(parent, text, cls) {
    var p = document.createElement('p');
    p.textContent = text;
    if (cls) { p.className = cls; }
    parent.appendChild(p);
  }

  function draw() {
    var box = document.getElementById('printers');
    box.textContent = '';
    lastPrinters.forEach(function (printer) {
      var div = document.createElement('div');
      var result = results[printer.device_name];
      div.className = 'printer' + (printer.failed || (result && result.bad) ? ' has-failure' : '');
      var h = document.createElement('h2');
      h.textContent = printer.name;
      div.appendChild(h);
      if (printer.last_job) {
        line(div, 'Last: ' + printer.last_job.code + ' ' + printer.last_job.status);
        if (printer.last_job.error) { line(div, printer.last_job.error, 'fail'); }
      } else {
        line(div, 'No jobs yet.');
      }
      if (printer.failed) {
        line(div, printer.failed + ' failed in the last hour.', 'fail');
      }
      if (result) { line(div, result.text, result.bad ? 'fail' : ''); }
      box.appendChild(div);
    });
    var agent = document.getElementById('agent-status');
    agent.textContent = agentProblem;
    agent.hidden = !agentProblem;
    document.getElementById('agent-names').textContent =
      agentNames.length ? agentNames.join(', ') : 'none listed';
  }

  function setApp(text, bad) {
    var el = document.getElementById('app-status');
    el.textContent = text;
    el.className = 'notice' + (bad ? ' bad' : '');
  }

  function tick() {
    if (busy) { return; }
    busy = true;
    post(COLLECT_URL, { skip_printer_ids: sendingIds() }).then(function (data) {
      if (!data.ok) {
        setApp(data.message || NO_APP, true);
        return;
      }
      setApp('Connected to the app.', false);
      lastPrinters = data.printers;
      startAll(data.jobs);
    }).catch(function () {
      setApp(NO_APP, true);
    }).then(function () {
      draw();
      busy = false;
    });
  }

  draw();
  refreshAgent();
  setInterval(refreshAgent, 10000);
  tick();
  setInterval(tick, 1000);
})();
