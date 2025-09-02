import React, { useEffect, useState } from 'react'
import ShipmentList from './components/ShipmentList'
import MapView from './components/MapView'

export default function App() {
  const [shipments, setShipments] = useState([])
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    fetch('/api/shipments')
      .then(res => res.json())
      .then(data => setShipments(data))
  }, [])

  return (
    <div style={{ display: 'flex', height: '100vh' }}>
      <ShipmentList shipments={shipments} onSelect={setSelected} />
      <MapView shipment={selected} />
    </div>
  )
}
