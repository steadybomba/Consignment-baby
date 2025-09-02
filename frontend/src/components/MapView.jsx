import React, { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
export default function MapView({ shipment }){
  const ref = useRef(null)
  useEffect(()=>{
    if(!shipment) return
    const el = ref.current
    if(!el) return
    el.innerHTML = ''
    const map = L.map(el).setView([ (shipment.origin.lat + shipment.destination.lat)/2, (shipment.origin.lng + shipment.destination.lng)/2 ], 3)
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(map)
    const pts = [ [shipment.origin.lat, shipment.origin.lng], ...shipment.checkpoints.map(c=>[c.lat,c.lng]), [shipment.destination.lat, shipment.destination.lng] ]
    L.polyline(pts).addTo(map)
    L.marker([shipment.origin.lat, shipment.origin.lng]).addTo(map).bindPopup('Origin')
    shipment.checkpoints.forEach(c=>L.marker([c.lat,c.lng]).addTo(map).bindPopup(c.label))
    L.marker([shipment.destination.lat, shipment.destination.lng]).addTo(map).bindPopup('Destination')
    map.fitBounds(pts, {padding:[50,50]})
    return ()=> map.remove()
  }, [shipment])
  return <div ref={ref} style={{flex:1, height:'100vh'}} />
}
