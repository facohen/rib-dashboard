const PROVINCIAS = [
    { p: 'Buenos Aires', d: ['La Matanza', 'Lomas de Zamora', 'Quilmes', 'General Pueyrredón', 'Almirante Brown'] },
    { p: 'Córdoba', d: ['Capital', 'Río Cuarto', 'San Justo'] },
    { p: 'Santa Fe', d: ['Rosario', 'La Capital', 'Rafaela'] },
    { p: 'Mendoza', d: ['Capital', 'Godoy Cruz', 'Luján de Cuyo'] },
    { p: 'Tucumán', d: ['Capital', 'Yerba Buena'] },
    { p: 'Salta', d: ['Capital', 'Orán'] },
    { p: 'Chaco', d: ['San Fernando', 'Comandante Fernández'] },
    { p: 'Corrientes', d: ['Capital', 'Goya'] },
    { p: 'Misiones', d: ['Capital', 'Eldorado'] },
    { p: 'Jujuy', d: ['Dr. Manuel Belgrano', 'Palpalá'] }
];

const SECRETARIAS = [
    'Secretaría de Inclusión Social',
    'Secretaría de Desarrollo Humano',
    'Secretaría de Economía Social'
];

const PROGRAMAS_DEF = [
    { id: 1, n: 'Asignación Universal por Hijo', s: 0 },
    { id: 2, n: 'Becas PROGRESAR', s: 0 },
    { id: 3, n: 'Tarjeta Alimentar', s: 0 },
    { id: 4, n: 'Pensiones no Contributivas', s: 1 },
    { id: 5, n: 'SUMAR – Salud', s: 1 },
    { id: 6, n: 'Hacemos Futuro', s: 1 },
    { id: 7, n: 'Crédito ARGENTA', s: 2 },
    { id: 8, n: 'Plan Potenciar Trabajo', s: 2 }
];

const NOMBRES_M = ['Carlos', 'Juan', 'Diego', 'Martín', 'Luis', 'Pedro', 'Roberto', 'Sergio', 'Mario', 'Daniel'];
const NOMBRES_F = ['María', 'Ana', 'Laura', 'Sandra', 'Patricia', 'Claudia', 'Carolina', 'Gabriela', 'Valeria', 'Marcela'];
const APELLIDOS = ['García', 'Rodríguez', 'González', 'Fernández', 'López', 'Martínez', 'Sánchez', 'Pérez', 'Gómez', 'Díaz'];
const SEXOS = ['M', 'M', 'M', 'F', 'F', 'F', 'F', 'F', 'X', 'NI'];

const PERIODOS = ['2026-01', '2026-02', '2026-03'];

// Reglas de incompatibilidad: [A, B]
const INCOMPATIBILITIES = [
    [1, 4], // AUH vs PNC
    [2, 8], // PROGRESAR vs POTENCIAR
    [3, 6], // ALIMENTAR vs HACEMOS
    [7, 8]  // ARGENTA vs POTENCIAR
];

// Utils
function rnd(a, b) { return Math.floor(Math.random() * (b - a + 1)) + a; }
function pick(arr) { return arr[rnd(0, arr.length - 1)]; }
function pad(n) { return n.toString().padStart(2, '0'); }

function getAgeAndBirth(groupIdx) {
    const ranges = [[0, 12], [13, 29], [30, 59], [60, 85]];
    const [lo, hi] = ranges[groupIdx];
    const age = rnd(lo, hi);
    const now = new Date(2026, 2, 31); // Corte 2026-03-31
    const birthYear = now.getFullYear() - age;
    return { age, bd: `${birthYear}-${pad(rnd(1, 12))}-${pad(rnd(1, 28))}` };
}

function getCuil(idx) {
    const pfx = ['20', '23', '24', '27'][idx % 4];
    const mid = (20000000 + idx).toString().padStart(8, '0');
    return `${pfx}${mid}0`;
}

// Generate DB
const DB = {
    beneficiaries: [],
    programs: PROGRAMAS_DEF.map(p => ({ id: p.id, nombre: p.n, secretaria: SECRETARIAS[p.s] })),
    secrets: SECRETARIAS,
    benefits: [],
    payments: [],
    rules: INCOMPATIBILITIES.map(([a, b]) => ({
        a, b, desc: `${PROGRAMAS_DEF.find(p => p.id === a).n} incompatible con ${PROGRAMAS_DEF.find(p => p.id === b).n}`
    }))
};

// Beneficiarios (300)
// Grupos: 0(20%), 1(30%), 2(35%), 3(15%)
const grupos = Array(60).fill(0).concat(Array(90).fill(1), Array(105).fill(2), Array(45).fill(3));
for (let i = grupos.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [grupos[i], grupos[j]] = [grupos[j], grupos[i]];
}

for (let i = 0; i < 300; i++) {
    const sx = pick(SEXOS);
    const ns = sx === 'M' ? NOMBRES_M : sx === 'F' ? NOMBRES_F : [...NOMBRES_M, ...NOMBRES_F];
    const prov = PROVINCIAS[i % PROVINCIAS.length];
    const { age, bd } = getAgeAndBirth(grupos[i]);

    DB.beneficiaries.push({
        id: i + 1,
        cuil: getCuil(i),
        nombre: pick(ns),
        apellido: pick(APELLIDOS),
        sexo: sx,
        fecha_nacimiento: bd,
        edad: age,
        provincia: prov.p,
        departamento: pick(prov.d),
        cp: (1000 + rnd(0, 8999)).toString()
    });
}

// Prestaciones y pagos
PERIODOS.forEach(periodo => {
    const [y, m] = periodo.split('-');

    DB.beneficiaries.forEach((b, i) => {
        // 5% no identificados (sin ID de beneficiario o con CUIL nulo)
        if (i >= 285) {
            const pid = pick(DB.programs).id;
            DB.benefits.push({
                id: DB.benefits.length + 1,
                beneficiary_id: null,
                cuil_raw: Math.random() > 0.5 ? '00000000000' : null,
                program_id: pid,
                periodo,
                estado: 'ACTIVO'
            });
            return;
        }

        // Concentración
        let cant = 1;
        if (i >= 150 && i < 240) cant = 2; // 30%
        if (i >= 240) cant = 3; // 15%

        let pids = [...DB.programs].sort(() => 0.5 - Math.random()).slice(0, cant).map(p => p.id);

        // Incompatibilidad forzada en 2026-03 (casos 220 a 239)
        if (periodo === '2026-03' && i >= 220 && i < 240) {
            if (cant < 2) pids.push(DB.programs.find(p => !pids.includes(p.id)).id);
            pids[0] = 1; pids[1] = 4; // AUH y PNC
        }

        pids.forEach(pid => {
            const isActivo = Math.random() > 0.08; // 92% activo
            DB.benefits.push({
                id: DB.benefits.length + 1,
                beneficiary_id: b.id,
                cuil_raw: b.cuil,
                program_id: pid,
                periodo,
                estado: isActivo ? 'ACTIVO' : 'INACTIVO'
            });

            if (isActivo) {
                const p = DB.programs.find(prog => prog.id === pid);
                const base = p.secretaria.includes('Inclusión') ? 80000 : p.secretaria.includes('Desarrollo') ? 95000 : 60000;
                const monto = base + rnd(-10000, 20000);
                DB.payments.push({
                    id: DB.payments.length + 1,
                    beneficiary_id: b.id,
                    program_id: pid,
                    periodo,
                    fecha_pago: `${y}-${m}-${pad(rnd(1, 28))}`,
                    monto
                });
            }
        });
    });
});

console.log('Mock DB generated:', {
    bens: DB.beneficiaries.length,
    bens_with_benefits: new Set(DB.benefits.map(b => b.beneficiary_id)).size,
    benefits: DB.benefits.length,
    payments: DB.payments.length
});
