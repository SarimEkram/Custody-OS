// Static REFERENCE data from DEMO.md / DATA_MODEL.md seed. Used only for map layout
// and display names until the backend supplies locations/samples via GET /state.
// It is never shown as ledger activity or scanner evidence.
import type { Identity, LabLocation, Sample } from './types'

export const IDENTITIES: Identity[] = [
  { _id: 'user:jlee', name: 'Jordan Lee', role: 'lab_tech', roleLabel: 'Lab technician', clearances: ['standard', 'controlled'] },
  { _id: 'user:mokafor', name: 'M. Okafor', role: 'supervisor', roleLabel: 'Supervisor', clearances: ['standard', 'controlled', 'forensic'] },
]

// Map coordinates live in a 760 x 420 viewBox.
export const SEED_LOCATIONS: LabLocation[] = [
  { _id: 'FZ-A', name: 'Freezer A', kind: 'freezer', certifications: ['BSL-2', 'FORENSIC'], map: { x: 110, y: 90 } },
  { _id: 'FZ-B', name: 'Freezer B', kind: 'freezer', certifications: ['BSL-2', 'FORENSIC'], map: { x: 110, y: 330 } },
  { _id: 'CORR-1', name: 'Corridor', kind: 'corridor', certifications: [], map: { x: 380, y: 210 } },
  { _id: 'SEQ-3', name: 'Sequencing Station 3', kind: 'station', certifications: ['BSL-2'], map: { x: 650, y: 90 } },
  { _id: 'FOR-LAB', name: 'Forensics Lab', kind: 'lab', certifications: ['FORENSIC'], map: { x: 650, y: 330 } },
]

export const SEED_SAMPLES: Sample[] = [
  { _id: 'TS-1042', type: 'tissue', description: 'Tissue sample', restriction: 'standard', location_id: 'FZ-B', max_minutes_out: 20 },
  { _id: 'EV-2201', type: 'evidence', description: 'DNA swab, case 24-1187', restriction: 'forensic', location_id: 'FZ-B', max_minutes_out: 20 },
  { _id: 'TS-1077', type: 'tissue', description: 'Tissue sample (short clock)', restriction: 'controlled', location_id: 'FZ-A', max_minutes_out: 3 },
]

export const DEFAULT_GRANT_ID = 'GRANT-0007'
