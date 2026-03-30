// APP STATE
let currentUser = null;
let currentPeriod = '2026-03';
let currentFiltros = { cuil: '', prov: '', sexo: '', prog: '' };
let globalChartFilters = {
    'c-secretaria': null, // Nombre de la secretaría seleccionada
    'c-sexo': null,       // Sexo seleccionado (M, F, X, NI)
    'c-prog': null,       // Programa seleccionado
    'c-prov': null,       // Provincia seleccionada
    'c-etario': null,     // Grupo Etario seleccionado
    'c-depto': null,      // Departamento seleccionado (ej "La Matanza (BUE)")
    'c-heatmap': null     // Heatmap: no cruza filtros, solo visualización
};
let currentPage = 1;
const PAGE_SIZE = 15;

const GEO_COORDS = {
    'La Matanza (Bue)': [-34.70, -58.60],
    'Lomas de Zamora (Bue)': [-34.76, -58.40],
    'Quilmes (Bue)': [-34.72, -58.26],
    'General Pueyrredón (Bue)': [-38.00, -57.55],
    'Almirante Brown (Bue)': [-34.82, -58.38],
    'Capital (Cór)': [-31.41, -64.18],
    'Río Cuarto (Cór)': [-33.13, -64.35],
    'San Justo (Cór)': [-31.43, -62.08],
    'Rosario (San)': [-32.95, -60.64],
    'La Capital (San)': [-31.63, -60.70],
    'Rafaela (San)': [-31.25, -61.48],
    'Capital (Men)': [-32.89, -68.84],
    'Godoy Cruz (Men)': [-32.92, -68.84],
    'Luján de Cuyo (Men)': [-33.04, -68.88],
    'Capital (Tuc)': [-26.82, -65.22],
    'Yerba Buena (Tuc)': [-26.81, -65.31],
    'Capital (Sal)': [-24.78, -65.41],
    'Orán (Sal)': [-23.13, -64.32],
    'San Fernando (Cha)': [-27.45, -58.98],
    'Comandante Fernández (Cha)': [-26.78, -60.44],
    'Capital (Cor)': [-27.46, -58.83],
    'Goya (Cor)': [-29.14, -59.26],
    'Capital (Mis)': [-27.36, -55.89],
    'Eldorado (Mis)': [-26.40, -54.62],
    'Dr. Manuel Belgrano (Juj)': [-24.18, -65.30],
    'Palpalá (Juj)': [-24.25, -65.20]
};
let myLeafletMap = null;
let leafletLayerGroup = null;

const VIEWS = ['login', 'dashboard', 'nominal'];

// ── INIT ──
document.addEventListener('DOMContentLoaded', () => {
    // Selectores topbar dashboard
    const pSelect = document.getElementById('periodSelect');
    pSelect.innerHTML = PERIODOS.map(p => `<option ${p === currentPeriod ? 'selected' : ''}>${p}</option>`).join('');

    // Selectores topbar nominal
    const nSelect = document.getElementById('nomPeriodSelect');
    nSelect.innerHTML = PERIODOS.map(p => `<option ${p === currentPeriod ? 'selected' : ''}>${p}</option>`).join('');

    // Filtros nominal
    const fProv = document.getElementById('fProv');
    fProv.innerHTML += PROVINCIAS.map(p => `<option value="${p.p}">${p.p}</option>`).join('');
    const fProg = document.getElementById('fProg');
    if (fProg) fProg.innerHTML += DB.programs.map(p => `<option value="${p.id}">${p.nombre}</option>`).join('');

    // Theme Setup
    const savedTheme = localStorage.getItem('siis-theme');
    if (savedTheme === 'light') {
        document.body.classList.add('light-theme');
        updateThemeIcons('light');
    }

    showView('login');
});

function toggleTheme() {
    const isLight = document.body.classList.toggle('light-theme');
    const newTheme = isLight ? 'light' : 'dark';
    localStorage.setItem('siis-theme', newTheme);
    updateThemeIcons(newTheme);

    // Si estamos en dashboard, lo recargamos para que Chart.js tome colores nuevos
    if (document.getElementById('view-dashboard')?.style.display === 'flex') {
        loadDashboard();
    }
}

function updateThemeIcons(theme) {
    const iconStr = theme === 'light' ? '🌙' : '☀️';
    const ic1 = document.getElementById('themeIcon');
    const ic2 = document.getElementById('themeIconNom');
    if (ic1) ic1.textContent = iconStr;
    if (ic2) ic2.textContent = iconStr;
}

// ── ROUTING ──
function showView(viewId) {
    VIEWS.forEach(v => document.getElementById(`view-${v}`).style.display = 'none');
    document.getElementById(`view-${viewId}`).style.display = 'flex';

    if (viewId === 'dashboard') loadDashboard();
    if (viewId === 'nominal') {
        if (currentUser?.role !== 'admin') return showView('dashboard');
        renderNominal();
    }
}

// ── AUTH ──
function doLogin() {
    const loginSelect = document.getElementById('loginUser');
    const email = loginSelect ? loginSelect.value : document.getElementById('loginEmail').value;
    const err = document.getElementById('login-error');
    if (email.includes('admin')) {
        currentUser = { email, role: 'admin', nombre: 'Admin RIB' };
        setupSession();
        showView('dashboard');
    } else if (email.includes('user')) {
        currentUser = { email, role: 'user', nombre: 'Analista Demo' };
        setupSession();
        showView('dashboard');
    } else {
        err.textContent = 'Credenciales incorrectas (usá los demos de abajo)';
        err.style.display = 'block';
    }
}

function doLogout() {
    currentUser = null;
    showView('login');
}

function setupSession() {
    const initVars = (idAvatar, idName, idRoleBadge) => {
        const av = document.getElementById(idAvatar);
        const nm = document.getElementById(idName);
        const bd = document.getElementById(idRoleBadge);
        if (av) av.textContent = currentUser.nombre.charAt(0).toUpperCase();
        if (nm) nm.textContent = currentUser.nombre;
        if (bd) {
            bd.textContent = currentUser.role;
            bd.className = `role-badge ${currentUser.role}`;
        }
    };
    initVars('userAvatar', 'userName', 'roleBadge');
    initVars('userAvatar2', 'userName2', null);

    const nLink = document.getElementById('nav-nominal-link');
    if (nLink) nLink.style.display = currentUser.role === 'admin' ? 'flex' : 'none';
}

function showDashboard() { showView('dashboard'); }
function showNominal() { showView('nominal'); }

function onPeriod(p) {
    currentPeriod = p;
    document.getElementById('periodSelect').value = p;
    document.getElementById('nomPeriodSelect').value = p;
    if (VIEWS.some(v => document.getElementById(`view-${v}`).style.display === 'flex' && v === 'dashboard')) loadDashboard();
    else renderNominal();
}

// ── NUM FORMATTERS ──
function fmtNum(n) { return (n || 0).toLocaleString('es-AR', { minimumFractionDigits: 0 }); }
function fmt$$(n) { return '$' + (n || 0).toLocaleString('es-AR', { minimumFractionDigits: 0 }); }
function fmtBig(n) {
    if (n >= 1e6) return '$' + (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return '$' + (n / 1e3).toFixed(1) + 'k';
    return '$' + n;
}

// ── CHART.JS UTILS ──
let charts = {};
const COLORS = ['#4a67ff', '#7c3aed', '#10b981', '#f59e0b', '#ef4444', '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#a855f7'];

function renderChart(id, type, labels, data, colors, isHz = false) {
    const ctx = document.getElementById(id);
    if (!ctx) return;
    if (charts[id]) charts[id].destroy();

    const isLight = document.body.classList.contains('light-theme');
    const textColor = isLight ? '#4b5563' : 'rgba(255,255,255,0.6)';
    const gridColor = isLight ? 'rgba(0,0,0,0.06)' : 'rgba(255,255,255,0.04)';
    const datalabelColor = isLight ? '#1f2937' : '#ffffff';
    const datalabelShadow = isLight ? 'rgba(255,255,255,0.8)' : 'rgba(0,0,0,0.8)';

    const bg = type === 'bar' ? (colors || COLORS).map(c => c + '99') : (colors || COLORS);
    const bd = type === 'bar' ? (colors || COLORS) : (isLight ? '#ffffff' : '#1a1d2a');

    charts[id] = new Chart(ctx, {
        type,
        data: {
            labels,
            datasets: [{ data, backgroundColor: bg, borderColor: bd, borderWidth: type === 'doughnut' ? 2 : 1, borderRadius: type === 'bar' ? 4 : 0 }]
        },
        options: {
            indexAxis: isHz ? 'y' : 'x',
            responsive: true, maintainAspectRatio: false,
            onClick: (event, elements) => {
                if (elements.length > 0) {
                    const idx = elements[0].index;
                    const val = charts[id].data.labels[idx];

                    if (globalChartFilters[id] === val) {
                        globalChartFilters[id] = null;
                    } else {
                        globalChartFilters[id] = val;
                    }
                    loadDashboard();
                }
            },
            plugins: {
                legend: { display: type === 'doughnut', position: 'bottom', labels: { color: textColor, font: { size: 11 }, boxWidth: 10 } },
                tooltip: { backgroundColor: isLight ? '#1f2937' : '#1a1d2a', borderColor: isLight ? 'rgba(0,0,0,0.1)' : 'rgba(255,255,255,0.1)', borderWidth: 1, titleColor: '#fff', bodyColor: 'rgba(255,255,255,0.7)' },
                datalabels: {
                    color: datalabelColor,
                    font: { weight: 'bold', size: 11 },
                    formatter: (value, context) => {
                        if (value <= 0) return '';
                        const dataArr = context.chart.data.datasets[0].data;
                        const sum = dataArr.reduce((a, b) => a + Number(b), 0);
                        const percentage = ((value * 100) / sum).toFixed(1) + '%';
                        return percentage;
                    },
                    anchor: type === 'bar' ? (isHz ? 'end' : 'end') : 'center',
                    align: type === 'bar' ? (isHz ? 'start' : 'start') : 'center',
                    offset: type === 'bar' ? 4 : 0,
                    textShadowBlur: 4,
                    textShadowColor: datalabelShadow
                }
            },
            scales: type === 'bar' ? {
                x: { ticks: { color: textColor, font: { size: 10 } }, grid: { color: gridColor } },
                y: { ticks: { color: textColor, font: { size: 10 } }, grid: { display: !isHz, color: gridColor } }
            } : {}
        }
    });
}

// ── INDICADORES ENGINE ──
function loadDashboard() {
    document.getElementById('periodSelect').value = currentPeriod;

    // Mostrar el clear-filters-btn si hay alguno aplicado
    renderActiveFilters();

    // Filtrar base
    const bnf = DB.benefits.filter(b => b.periodo === currentPeriod);
    const act = bnf.filter(b => b.estado === 'ACTIVO');
    let pay = DB.payments.filter(p => p.periodo === currentPeriod);

    // Aplicar Filtros Cruzados (globalChartFilters) a act y pay
    const fSec = globalChartFilters['c-secretaria'];
    const fSex = globalChartFilters['c-sexo'];
    const fPrg = globalChartFilters['c-prog'];
    const fPrv = globalChartFilters['c-prov'];
    const fDpt = globalChartFilters['c-depto'];
    const fEta = globalChartFilters['c-etario']; // Niñez, Jóvenes, Adultos, Mayores

    let validBnf = act.filter(b => b.beneficiary_id !== null);

    // Función auxiliar para saber el grupo etario completo
    const getGrupo = (e) => {
        if (e <= 12) return 'Niñez';
        if (e <= 29) return 'Jóvenes';
        if (e <= 59) return 'Adultos';
        return 'Mayores';
    };

    // Filtrar válidos
    validBnf = validBnf.filter(b => {
        const p = DB.programs.find(prog => prog.id === b.program_id);
        const ben = DB.beneficiaries.find(x => x.id === b.beneficiary_id);

        if (fSec && (!p || !p.secretaria.includes(fSec))) return false;
        if (fSex && (!ben || ben.sexo !== fSex)) return false;
        if (fPrg && (!p || p.nombre !== fPrg)) return false;
        if (fPrv && (!ben || ben.provincia !== fPrv)) return false;
        // departamento filter removed
        if (fEta && (!ben || getGrupo(ben.edad) !== fEta)) return false;

        return true;
    });

    // Filtramos payments basado en las mismas condiciones
    pay = pay.filter(x => {
        const p = DB.programs.find(prog => prog.id === x.program_id);
        const ben = DB.beneficiaries.find(bx => bx.id === x.beneficiary_id);
        if (fSec && (!p || !p.secretaria.includes(fSec))) return false;
        if (fSex && (!ben || ben.sexo !== fSex)) return false;
        if (fPrg && (!p || p.nombre !== fPrg)) return false;
        if (fPrv && (!ben || ben.provincia !== fPrv)) return false;
        // departamento filter removed
        if (fEta && (!ben || getGrupo(ben.edad) !== fEta)) return false;
        return true;
    });
    const unqBenefsIds = [...new Set(validBnf.map(b => b.beneficiary_id))];
    const cov = unqBenefsIds.length;

    // Totales
    const totMonto = pay.reduce((s, x) => s + x.monto_prestacion, 0);
    const promP = cov > 0 ? (validBnf.length / cov) : 0;
    const promM = cov > 0 ? (totMonto / cov) : 0;

    // I-14 Err
    const inv = bnf.filter(b => !b.beneficiary_id || !b.cuil_raw || b.cuil_raw.length < 11).length;
    const tId = bnf.length > 0 ? (inv / bnf.length) * 100 : 0;

    // Incompatibilidades
    let casosInc = 0;
    const progMap = {}; // { u_id: [p1, p2] }
    validBnf.forEach(b => {
        if (!progMap[b.beneficiary_id]) progMap[b.beneficiary_id] = [];
        progMap[b.beneficiary_id].push(b.program_id);
    });

    for (let uid in progMap) {
        const pids = progMap[uid];
        if (pids.length < 2) continue;
        let conflict = false;
        DB.rules.forEach(r => {
            if (pids.includes(r.a) && pids.includes(r.b)) conflict = true;
        });
        if (conflict) casosInc++;
    }

    // Set KPIs
    document.getElementById('k-cob').textContent = fmtNum(cov);
    document.getElementById('k-prest').textContent = promP.toFixed(2);
    document.getElementById('k-pmonto').textContent = fmtBig(promM);
    document.getElementById('k-total').textContent = fmtBig(totMonto);
    document.getElementById('k-noid').textContent = tId.toFixed(1) + '%';
    document.getElementById('k-incomp').textContent = fmtNum(casosInc);

    // ── CHARTS ──
    // I-02 Sec
    const secC = {};
    DB.secrets.forEach(s => secC[s] = 0);
    unqBenefsIds.forEach(uid => {
        const ubp = validBnf.find(b => b.beneficiary_id === uid);
        if (ubp) {
            const p = DB.programs.find(prog => prog.id === ubp.program_id);
            if (p) secC[p.secretaria]++;
        }
    });
    renderChart('c-secretaria', 'doughnut', Object.keys(secC).map(k => k.replace('Secretaría de ', '')), Object.values(secC));

    // I-12 Sex
    const sexC = { M: 0, F: 0, X: 0, NI: 0 };
    unqBenefsIds.forEach(uid => {
        const ben = DB.beneficiaries.find(x => x.id === uid);
        if (ben && sexC[ben.sexo] !== undefined) sexC[ben.sexo]++;
    });
    renderChart('c-sexo', 'doughnut', Object.keys(sexC), Object.values(sexC), ['#4a67ff', '#ec4899', '#10b981', '#f59e0b']);

    // Concentración
    let c1 = 0, c2 = 0, c3 = 0;
    Object.values(progMap).forEach(arr => {
        if (arr.length === 1) c1++;
        else if (arr.length === 2) c2++;
        else if (arr.length >= 3) c3++;
    });
    renderChart('c-conc', 'doughnut', ['1 Prest.', '2 Prest.', '3+ Prest.'], [c1, c2, c3], ['#4a67ff', '#7c3aed', '#10b981']);

    // I-11 Etario
    const edC = { 'Niñez': 0, 'Jóvenes': 0, 'Adultos': 0, 'Mayores': 0 };
    unqBenefsIds.forEach(uid => {
        const ben = DB.beneficiaries.find(x => x.id === uid);
        if (!ben) return;
        const e = ben.edad;
        if (e <= 12) edC['Niñez']++;
        else if (e <= 29) edC['Jóvenes']++;
        else if (e <= 59) edC['Adultos']++;
        else edC['Mayores']++;
    });
    renderChart('c-etario', 'bar', Object.keys(edC), Object.values(edC), ['#4a67ff', '#7c3aed', '#10b981', '#f59e0b'], false);

    // I-13 Prog
    const prC = {};
    DB.programs.forEach(p => prC[p.nombre] = 0);
    validBnf.forEach(b => {
        const pname = DB.programs.find(x => x.id === b.program_id)?.nombre;
        if (pname) prC[pname]++;
    });
    renderChart('c-prog', 'bar', Object.keys(prC), Object.values(prC), null, true);

    // I-03 Prov
    const pvC = {};
    PROVINCIAS.forEach(p => pvC[p.p] = 0);
    unqBenefsIds.forEach(uid => {
        const ben = DB.beneficiaries.find(x => x.id === uid);
        if (ben && pvC[ben.provincia] !== undefined) pvC[ben.provincia]++;
    });
    // Ordenar by value
    const pvArr = Object.entries(pvC).sort((a, b) => b[1] - a[1]);
    renderChart('c-prov', 'bar', pvArr.map(x => x[0]), pvArr.map(x => x[1]), null, true);

    // I-04 Depto
    const dpC = {};
    unqBenefsIds.forEach(uid => {
        const ben = DB.beneficiaries.find(x => x.id === uid);
        if (ben) {
            const k = `${ben.provincia}`;
            dpC[k] = (dpC[k] || 0) + 1;
        }
    });
    const dpArr = Object.entries(dpC).sort((a, b) => b[1] - a[1]).slice(0, 10);
    renderChart('c-depto', 'bar', dpArr.map(x => x[0]), dpArr.map(x => x[1]), null, true);

    // I-17 Mapa Geográfico
    renderGeoMap(unqBenefsIds);

    // I-16 Evolución (Timeline con filtros cruzados aplicados)
    const perHist = PERIODOS.slice().reverse(); // De más antiguo a actual
    const histData = perHist.map(p => {
        // Para cada periodo calculamos el total de pagos con los mismos filtros activos
        let periodPay = DB.payments.filter(y => y.periodo === p);
        // Aplicar los mismos filtros cruzados
        periodPay = periodPay.filter(x => {
            const prog = DB.programs.find(prog => prog.id === x.program_id);
            const ben = DB.beneficiaries.find(bx => bx.id === x.beneficiary_id);
            if (fSec && (!prog || !prog.secretaria.includes(fSec))) return false;
            if (fSex && (!ben || ben.sexo !== fSex)) return false;
            if (fPrg && (!prog || prog.nombre !== fPrg)) return false;
            if (fPrv && (!ben || ben.provincia !== fPrv)) return false;
            // departamento filter removed
            if (fEta && (!ben || getGrupo(ben.edad) !== fEta)) return false;
            return true;
        });
        return periodPay.reduce((s, y) => s + (y.monto || y.monto_prestacion || 0), 0);
    });

    // Renderizado de Curva (Area)
    const evCtx = document.getElementById('c-evolucion');
    if (evCtx) {
        if (charts['c-evolucion']) charts['c-evolucion'].destroy();
        const isLight = document.body.classList.contains('light-theme');
        const tc = isLight ? '#4b5563' : 'rgba(255,255,255,0.6)';
        const gc = isLight ? 'rgba(0,0,0,0.06)' : 'rgba(255,255,255,0.04)';

        let grad = evCtx.getContext('2d').createLinearGradient(0, 0, 0, 300);
        if (isLight) {
            grad.addColorStop(0, 'rgba(74, 103, 255, 0.4)');
            grad.addColorStop(1, 'rgba(74, 103, 255, 0.01)');
        } else {
            grad.addColorStop(0, 'rgba(74, 103, 255, 0.3)');
            grad.addColorStop(1, 'rgba(74, 103, 255, 0.01)');
        }

        // Escala dinámica: se adapta siempre a los datos filtrados
        const validVals = histData.filter(v => v > 0);
        const dataMin = validVals.length ? Math.min(...validVals) : 0;
        const dataMax = validVals.length ? Math.max(...validVals) : 1;
        const pad = (dataMax - dataMin) * 0.15 || dataMax * 0.1;
        const yMin = Math.max(0, dataMin - pad);
        const yMax = dataMax + pad;

        charts['c-evolucion'] = new Chart(evCtx, {
            type: 'line',
            data: {
                labels: perHist,
                datasets: [{
                    label: 'Pagos Liquidados',
                    data: histData,
                    borderColor: '#4a67ff',
                    backgroundColor: grad,
                    borderWidth: 3,
                    fill: true,
                    tension: 0.4,
                    pointBackgroundColor: '#fff',
                    pointBorderColor: '#4a67ff',
                    pointBorderWidth: 2,
                    pointRadius: 4,
                    pointHoverRadius: 6
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: isLight ? '#1f2937' : '#1a1d2a',
                        titleColor: '#fff', bodyColor: 'rgba(255,255,255,0.8)', padding: 10,
                        callbacks: { label: c => 'Total: ' + fmtBig(c.raw) }
                    },
                    datalabels: { display: false }
                },
                scales: {
                    x: { ticks: { color: tc, font: { size: 11 } }, grid: { display: false } },
                    y: {
                        ticks: { color: tc, font: { size: 11 }, callback: v => fmtBig(v) },
                        grid: { color: gc, borderDash: [4, 4] },
                        min: yMin,
                        max: yMax
                    }
                }
            }
        });
    }
}

// ── GEO MAP ──
function renderGeoMap(unqBenefsIds) {
    const mapDiv = document.getElementById('map-argentina');
    if (!mapDiv) return;

    const isLight = document.body.classList.contains('light-theme');
    const tileUrl = isLight 
        ? 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png'
        : 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';

    if (!myLeafletMap) {
        myLeafletMap = L.map('map-argentina').setView([-34.6037, -58.3816], 4);
        
        L.tileLayer(tileUrl, {
            attribution: '© OpenStreetMap contributors, © CARTO',
            subdomains: 'abcd',
            maxZoom: 19
        }).addTo(myLeafletMap);
        
        leafletLayerGroup = L.layerGroup().addTo(myLeafletMap);
        
        // Wait till visible to invalidate size
        setTimeout(() => myLeafletMap.invalidateSize(), 150);
    } else {
        myLeafletMap.eachLayer((layer) => {
            if (layer instanceof L.TileLayer) {
                layer.setUrl(tileUrl);
            }
        });
        leafletLayerGroup.clearLayers();
    }

    const dptosCount = {};
    unqBenefsIds.forEach(uid => {
        const ben = DB.beneficiaries.find(x => x.id === uid);
        if (ben) {
            const k = `${ben.provincia}`;
            dptosCount[k] = (dptosCount[k] || 0) + 1;
        }
    });

    let maxCount = Math.max(...Object.values(dptosCount), 1);
    
    // Create markers
    Object.keys(dptosCount).forEach(k => {
        // En nuestro GEO_COORDS usamos sufijos con Title case para provincia ej (Bue), (Cór)
        const coords = GEO_COORDS[k];
        if (coords) {
            const count = dptosCount[k];
            // Radius 8 to 30
            const radius = 8 + (count / maxCount) * 22;
            
            // Color de gradiente tipo heatmap: verde a rojo intenso
            const t = count / maxCount;
            // Verde (16, 185, 129) -> Amarillo -> Rojo (239, 68, 68)
            let r, g, b;
            if (t < 0.5) {
                const t2 = t * 2;
                r = Math.round(16 + (245 - 16) * t2);
                g = Math.round(185 + (158 - 185) * t2);
                b = Math.round(129 + (11 - 129) * t2);
            } else {
                const t2 = (t - 0.5) * 2;
                r = Math.round(245 + (239 - 245) * t2);
                g = Math.round(158 + (68 - 158) * t2);
                b = Math.round(11 + (68 - 11) * t2);
            }
            const fillColor = `rgb(${r},${g},${b})`;

            const circle = L.circleMarker(coords, {
                radius: radius,
                fillColor: fillColor,
                color: isLight ? '#ffffff' : '#1a1d2a',
                weight: 2,
                opacity: 0.9,
                fillOpacity: 0.65
            });

            circle.bindTooltip(`<b>${k}</b><br>Beneficiarios: <strong style="font-size:14px">${count}</strong>`, {
                direction: 'top'
            });

            leafletLayerGroup.addLayer(circle);
        }
    });

    const boundsCoords = Object.keys(dptosCount).map(k => GEO_COORDS[k]).filter(c => c);
    if (boundsCoords.length > 0) {
        myLeafletMap.fitBounds(L.latLngBounds(boundsCoords), { padding: [50, 50], maxZoom: 8 });
    }
}

function clearDashboardFilters() {
    Object.keys(globalChartFilters).forEach(k => globalChartFilters[k] = null);
    loadDashboard();
}

function renderActiveFilters() {
    const act = Object.values(globalChartFilters).filter(v => v !== null);
    let topFilterDiv = document.getElementById('dashFilters');

    if (act.length > 0) {
        if (!topFilterDiv) {
            topFilterDiv = document.createElement('div');
            topFilterDiv.id = 'dashFilters';
            topFilterDiv.style = "display: flex; gap: 8px; margin-left: 16px; align-items: center;";
            document.querySelector('.period-sel').after(topFilterDiv);
        }
        topFilterDiv.innerHTML = `
            ${act.map(v => `<span style="background:rgba(74, 103, 255, 0.2); border: 1px solid rgba(74, 103, 255, 0.4); color:#93a8ff; padding: 4px 10px; border-radius:12px; font-size: 11px; font-weight:600;">${v}</span>`).join('')}
            <button onclick="clearDashboardFilters()" style="background:none; border:none; color:rgba(255,255,255,0.4); text-decoration:underline; font-size:11px; cursor:pointer;" onmouseover="this.style.color='#fff'" onmouseout="this.style.color='rgba(255,255,255,0.4)'">Limpiar Filtros✕</button>
        `;
    } else if (topFilterDiv) {
        topFilterDiv.remove();
    }
}


// ── TABLA NOMINAL ──
function getNominalData() {
    currentPeriod = document.getElementById('nomPeriodSelect').value;
    const cuil = document.getElementById('fCuil').value.trim();
    const prov = document.getElementById('fProv').value;
    const sexo = document.getElementById('fSexo').value;
    const prog = document.getElementById('fProg').value; // ID

    // Pre-join
    const currentBnf = DB.benefits.filter(b => b.periodo === currentPeriod);

    // Agrupar prest activos por user en el periodo
    const userProgs = {};
    currentBnf.forEach(b => {
        if (!b.beneficiary_id) return;
        if (!userProgs[b.beneficiary_id]) userProgs[b.beneficiary_id] = [];
        userProgs[b.beneficiary_id].push(b);
    });

    return DB.beneficiaries.filter(u => {
        if (cuil && !u.cuil.includes(cuil)) return false;
        if (prov && u.provincia !== prov) return false;
        if (sexo && u.sexo !== sexo) return false;

        const ups = userProgs[u.id] || [];
        if (prog && !ups.some(p => p.program_id == prog)) return false;

        // Solo mostrar los que tienen ALGO de actividad/inactividad en este periodo
        if (ups.length === 0) return false;

        return true;
    }).map(u => ({ ...u, cant: (userProgs[u.id] || []).filter(x => x.estado === 'ACTIVO').length }));
}

function clearFilters() {
    document.getElementById('fCuil').value = '';
    document.getElementById('fProv').value = '';
    document.getElementById('fSexo').value = '';
    document.getElementById('fProg').value = '';
    renderNominal(1);
}

function renderNominal(page = 1) {
    currentPage = page;
    const data = getNominalData();
    document.getElementById('nomCount').textContent = fmtNum(data.length);

    const totalPages = Math.ceil(data.length / PAGE_SIZE) || 1;
    const tb = document.getElementById('nomBody');

    if (data.length === 0) {
        tb.innerHTML = `<tr><td colspan="7" class="empty-state">No se encontraron beneficiarios para los filtros aplicados.</td></tr>`;
        renderPagination(1, 1);
        return;
    }

    const start = (page - 1) * PAGE_SIZE;
    const slice = data.slice(start, start + PAGE_SIZE);

    tb.innerHTML = slice.map(b => {
        const tc = b.cant === 1 ? 'tag-1' : b.cant === 2 ? 'tag-2' : b.cant >= 3 ? 'tag-3p' : 'tag-ni';
        const sx = { M: ['M', 'tag-m'], F: ['F', 'tag-f'], X: ['X', 'tag-x'], NI: ['NI', 'tag-ni'] }[b.sexo];
        return `<tr onclick="openDrawer(${b.id})">
      <td><code class="td-m" style="font-size:12px">${b.cuil}</code></td>
      <td><strong>${b.apellido}</strong>, ${b.nombre}</td>
      <td><span class="tag ${sx[1]}">${sx[0]}</span></td>
      <td>${b.edad}</td>
      <td>${b.provincia}</td>
      <td class="td-m">${b.provincia}</td>
      <td><span class="tag ${tc}">${b.cant}</span></td>
    </tr>`;
    }).join('');

    renderPagination(page, totalPages);
}

function renderPagination(p, t) {
    document.getElementById('nomPageInfo').textContent = `Página ${p} de ${t}`;
    const wrap = document.getElementById('nomPagBtns');
    let h = `<button class="pag-btn" ${p === 1 ? 'disabled' : ''} onclick="renderNominal(${p - 1})">‹</button>`;

    const min = Math.max(1, p - 2);
    const max = Math.min(t, p + 2);

    for (let i = min; i <= max; i++) h += `<button class="pag-btn ${p === i ? 'active' : ''}" onclick="renderNominal(${i})">${i}</button>`;

    h += `<button class="pag-btn" ${p === t ? 'disabled' : ''} onclick="renderNominal(${p + 1})">›</button>`;
    wrap.innerHTML = h;
}

// ── DRAWER ──
function openDrawer(id) {
    const b = DB.beneficiaries.find(x => x.id === id);
    const bnf = DB.benefits.filter(x => x.beneficiary_id === id && x.periodo === currentPeriod);
    const pay = DB.payments.filter(x => x.beneficiary_id === id && x.periodo === currentPeriod);

    document.getElementById('overlay').classList.add('open');
    document.getElementById('drawer').classList.add('open');

    document.getElementById('drawerTitle').innerHTML = `${b.apellido}, ${b.nombre} <br><small class="td-m" style="font-weight:400;font-size:12px">${b.cuil}</small>`;

    const totAg = pay.reduce((s, x) => s + x.monto_prestacion, 0);

    document.getElementById('drawerBody').innerHTML = `
    <div class="info-grid">
      <div class="ii"><div class="lbl">Edad</div><div class="val">${b.edad} años (${b.fecha_nacimiento})</div></div>
      <div class="ii"><div class="lbl">Provincia</div><div class="val">${b.provincia}</div></div>
      <div class="ii"><div class="lbl">Depto</div><div class="val">${b.provincia}</div></div>
      <div class="ii"><div class="lbl">Total ${currentPeriod}</div><div class="val" style="color:var(--green)">${fmt$$(totAg)}</div></div>
    </div>
    
    <div class="d-section">Prestaciones (${currentPeriod})</div>
    ${bnf.length === 0 ? '<div class="empty-state">Sin prestaciones</div>' :
            bnf.map(x => {
                const p = DB.programs.find(p => p.id === x.program_id);
                const ec = x.estado === 'ACTIVO' ? 'e-activo' : 'e-inactivo';
                return `<div class="prest-item">
          <div><div class="prg">${p.nombre}</div><div class="sec">${p.secretaria}</div></div>
          <div class="${ec}">${x.estado}</div>
        </div>`;
            }).join('')
        }
    
    <div class="d-section">Pagos Liquidados (${currentPeriod})</div>
    ${pay.length === 0 ? '<div class="empty-state">Sin pagos</div>' :
            pay.map(x => {
                const p = DB.programs.find(p => p.id === x.program_id);
                return `<div class="pago-row">
          <div><div class="prg">${p.nombre}</div><div class="fecha">Fecha pago: ${x.fecha_pago}</div></div>
          <div class="pago-monto">${fmt$$(x.monto_prestacion)}</div>
        </div>`;
            }).join('')
        }
  `;
}

function closeDrawer() {
    document.getElementById('overlay').classList.remove('open');
    document.getElementById('drawer').classList.remove('open');
}

// ── EXP CSV ──
function exportCSV() {
    const data = getNominalData();
    if (data.length === 0) return alert('No hay datos para exportar.');

    let csv = 'CUIL,Apellido,Nombre,Sexo,Edad,Provincia,Cant.Prestaciones\n';
    data.forEach(b => {
        csv += `"${b.cuil}","${b.apellido}","${b.nombre}","${b.sexo}","${b.edad}","${b.provincia}","${b.cant}"\n`;
    });

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.setAttribute('href', url);
    link.setAttribute('download', `rub_nominal_${currentPeriod}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}
