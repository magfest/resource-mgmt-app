// Polls the app for label jobs and sends each to its named printer through
// Zebra Browser Print on this computer. All text is set with textContent
// because job text comes from people.
(function () {
  'use strict';
  var body = document.getElementById('station');
  var COLLECT_URL = body.dataset.collectUrl;
  var REPORT_URL = body.dataset.reportUrl;
  var AGENT = 'http://localhost:9100';
  var NO_AGENT = 'Browser Print is not running or has not accepted this site.';
  var NO_APP = 'Cannot reach the app. Retrying.';
  var busy = false;
  var agentNames = [];
  var agentProblem = '';
  var lastPrinters = [];
  var results = {};  // device_name -> {text, bad} from this page's own sends

  function post(url, payload) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function (r) {
      return r.json().then(function (data) { return data; }, function () {
        return { ok: false, message: NO_APP };
      });
    });
  }

  function available() {
    return fetch(AGENT + '/available').then(function (r) {
      if (!r.ok) { throw new Error('available'); }
      return r.json();
    }).then(function (data) { return data.printer || []; });
  }

  function writeLabel(device, zpl) {
    // Browser Print wants text/plain with a JSON string body.
    return fetch(AGENT + '/write', {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: JSON.stringify({ device: device, data: zpl })
    }).then(function (r) {
      if (!r.ok) { throw new Error('write'); }
    });
  }

  function report(id, ok, error) {
    return post(REPORT_URL, { job_id: id, ok: ok, error: error || '' })
      .catch(function () { /* the app fails the job after 60 seconds */ });
  }

  function sendJob(job, devices) {
    var device = null;
    for (var i = 0; i < devices.length; i++) {
      if (devices[i].name === job.device_name) { device = devices[i]; break; }
    }
    if (!device) {
      var msg = 'Browser Print has no printer named "' + job.device_name + '".';
      results[job.device_name] = { text: msg, bad: true };
      return report(job.id, false, msg);
    }
    return writeLabel(device, job.zpl).then(function () {
      results[job.device_name] = { text: 'Sent a label.', bad: false };
      return report(job.id, true);
    }, function () {
      var msg = 'Browser Print refused the label.';
      results[job.device_name] = { text: msg, bad: true };
      return report(job.id, false, msg);
    });
  }

  // One printer's jobs run in order; different printers run side by side.
  function sendAll(jobs, devices) {
    var chains = {};
    jobs.forEach(function (job) {
      var key = job.device_name;
      chains[key] = (chains[key] || Promise.resolve()).then(function () {
        return sendJob(job, devices);
      });
    });
    return Promise.all(Object.keys(chains).map(function (k) { return chains[k]; }));
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
    post(COLLECT_URL, {}).then(function (data) {
      if (!data.ok) {
        setApp(data.message || NO_APP, true);
        return;
      }
      setApp('Connected to the app.', false);
      lastPrinters = data.printers;
      if (!data.jobs.length) { return; }
      return available().then(function (devices) {
        agentNames = devices.map(function (d) { return d.name; });
        agentProblem = '';
        return sendAll(data.jobs, devices);
      }, function () {
        agentProblem = NO_AGENT;
        return sendAll(data.jobs, []);
      });
    }).catch(function () {
      setApp(NO_APP, true);
    }).then(function () {
      draw();
      busy = false;
    });
  }

  draw();
  tick();
  setInterval(tick, 1000);
})();
