import React, { useEffect, useState } from 'react'
import axios from 'axios'
import ShipmentList from './components/ShipmentList'
import MapView from './components/MapView'
export default function App(){
  const [shipments, setShipments] = useState([])
  const [selected, setSelected] = useState(null)
  useEffect(()=>{ fetchShipments() }, [])
  async function fetchShipments(){
    try{
      const res = await axios.get('/api/admin/shipments')
      setShipments(res.data)
    }catch(e){ console.error(e) }
  }
  return (
    <div style={{display:'flex',height:'100vh'}}>
      <ShipmentList shipments={shipments} onSelect={setSelected} />
      <MapView shipment={selected} />
    </div>
  )
}
