import React from 'react'
export default function ShipmentList({ shipments, onSelect }){
  return (
    <div style={{width:320, borderRight:'1px solid #222', padding:12, overflowY:'auto'}}>
      <h2>Shipments</h2>
      {shipments.map(s=>(
        <div key={s.tracking_number} style={{padding:8, border:'1px solid #333', marginBottom:8, cursor:'pointer'}} onClick={()=>onSelect(s)}>
          <strong>{s.tracking_number}</strong>
          <div className="muted">{s.title} — {s.status}</div>
        </div>
      ))}
    </div>
  )
}
