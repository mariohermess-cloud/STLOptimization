/**
 * Loads the repaired mesh the backend serves.
 *
 * The viewer deliberately loads `/api/models/{id}/mesh.stl` rather than the
 * file the user picked: that endpoint returns the mesh *after* repair, so
 * triangle index `i` in the browser is triangle index `i` in every analysis.
 * Face selections therefore round-trip exactly.
 */

import { useEffect, useState } from 'react'
import { BufferGeometry, Float32BufferAttribute } from 'three'
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js'

export interface LoadedGeometry {
  geometry: BufferGeometry
  faceCount: number
  size: [number, number, number]
  center: [number, number, number]
}

export function useModelGeometry(url: string | null): {
  data: LoadedGeometry | null
  error: string | null
  loading: boolean
} {
  const [data, setData] = useState<LoadedGeometry | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!url) {
      setData(null)
      setError(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)

    fetch(url)
      .then(async (response) => {
        if (!response.ok) throw new Error(`Mesh download failed (${response.status}).`)
        return response.arrayBuffer()
      })
      .then((buffer) => {
        if (cancelled) return
        const geometry = new STLLoader().parse(buffer)
        geometry.computeVertexNormals()
        geometry.computeBoundingBox()
        geometry.computeBoundingSphere()
        const box = geometry.boundingBox!
        const position = geometry.getAttribute('position')
        setData({
          geometry,
          faceCount: position.count / 3,
          size: [box.max.x - box.min.x, box.max.y - box.min.y, box.max.z - box.min.z],
          center: [
            (box.max.x + box.min.x) / 2,
            (box.max.y + box.min.y) / 2,
            (box.max.z + box.min.z) / 2,
          ],
        })
      })
      .catch((cause) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [url])

  useEffect(() => () => data?.geometry.dispose(), [data])

  return { data, error, loading }
}

/**
 * Build a geometry containing only the given triangles.
 *
 * STL geometry is non-indexed: triangle `i` owns vertices `3i .. 3i+2`, which
 * is what makes highlighting a face selection a direct slice of the position
 * buffer.
 */
export function subsetGeometry(source: BufferGeometry, faceIds: number[]): BufferGeometry | null {
  if (faceIds.length === 0) return null
  const position = source.getAttribute('position')
  const total = position.count / 3
  const values = new Float32Array(faceIds.length * 9)
  let cursor = 0
  for (const face of faceIds) {
    if (face < 0 || face >= total) continue
    for (let vertex = 0; vertex < 3; vertex += 1) {
      const index = face * 3 + vertex
      values[cursor] = position.getX(index)
      values[cursor + 1] = position.getY(index)
      values[cursor + 2] = position.getZ(index)
      cursor += 3
    }
  }
  if (cursor === 0) return null
  const geometry = new BufferGeometry()
  geometry.setAttribute('position', new Float32BufferAttribute(values.subarray(0, cursor), 3))
  geometry.computeVertexNormals()
  return geometry
}
