import React from 'react'

export default function ShipmentList({ shipments, onSelect }) {
  return (
    <div style={{ width: '30%', borderRight: '1px solid #ccc', padding: '10px' }}>
      <h2>Shipments</h2>
      {shipments.map(s => (
        <div key={s.tracking} onClick={() => onSelect(s)} style={{ cursor: 'pointer', marginBottom: '10px' }}>
          <strong>{s.tracking}</strong>
          <p>{s.title} - {s.status}</p>
        </div>
      ))}
    </div>
  )
}
