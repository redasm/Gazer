import React, { useEffect, useCallback } from 'react';
import ForceGraph3D from 'react-force-graph-3d';
import * as THREE from 'three';

const MemoryGraphCanvas = ({
    fgRef,
    graphData,
    typeColors,
    connectionMap,
    highlightNodes,
    highlightLinks,
    onNodeClick,
    onNodeHover,
}) => {
    useEffect(() => {
        const fg = fgRef.current;
        if (!fg) return;

        const renderer = fg.renderer();
        const rect = renderer.domElement.getBoundingClientRect();
        const frustumSize = 300;
        const aspect = rect.width / rect.height;

        const orthoCamera = new THREE.OrthographicCamera(
            -frustumSize * aspect / 2,
            frustumSize * aspect / 2,
            frustumSize / 2,
            -frustumSize / 2,
            0.1,
            10000
        );
        orthoCamera.position.set(0, 0, 500);
        orthoCamera.lookAt(0, 0, 0);

        fg.camera(orthoCamera);

        const handleResize = () => {
            const r = renderer.domElement.getBoundingClientRect();
            const a = r.width / r.height;
            orthoCamera.left = -frustumSize * a / 2;
            orthoCamera.right = frustumSize * a / 2;
            orthoCamera.top = frustumSize / 2;
            orthoCamera.bottom = -frustumSize / 2;
            orthoCamera.updateProjectionMatrix();
        };

        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, [fgRef]);

    const nodeThreeObject = useCallback((node) => {
        const connections = connectionMap[node.id] || 0;
        const active = highlightNodes.size === 0 || highlightNodes.has(node.id);
        const baseColor = typeColors[node.group] || '#8b5cf6';

        let geometry;
        if (node.group === 'root') {
            geometry = new THREE.OctahedronGeometry(3, 0);
        } else {
            const radius = Math.min(0.6 + Math.sqrt(connections) * 0.3, 2.5);
            geometry = new THREE.SphereGeometry(radius, 32, 24);
        }

        const material = new THREE.MeshPhongMaterial({
            color: active ? baseColor : '#2a2a35',
            transparent: true,
            opacity: active ? 0.92 : 0.12,
            shininess: node.group === 'root' ? 120 : 80,
            emissive: active ? baseColor : '#000',
            emissiveIntensity: active ? (node.group === 'root' ? 0.5 : 0.25) : 0,
        });

        return new THREE.Mesh(geometry, material);
    }, [connectionMap, highlightNodes, typeColors]);

    const getLinkColor = useCallback((link) => {
        if (highlightNodes.size === 0) return 'rgba(140,160,200,0.15)';
        return highlightLinks.has(link) ? 'rgba(200,210,240,0.5)' : 'rgba(80,80,100,0.05)';
    }, [highlightNodes, highlightLinks]);

    const getLinkWidth = useCallback((link) => {
        if (highlightNodes.size === 0) return 0.4;
        return highlightLinks.has(link) ? 0.8 : 0.1;
    }, [highlightNodes, highlightLinks]);

    const getLinkParticles = useCallback((link) => {
        return highlightLinks.has(link) ? 3 : 0;
    }, [highlightLinks]);

    const getLinkParticleWidth = useCallback((link) => {
        return highlightLinks.has(link) ? 1.2 : 0;
    }, [highlightLinks]);

    return (
        <ForceGraph3D
            ref={fgRef}
            graphData={graphData}
            nodeLabel={(n) => n.name || n.id}
            nodeThreeObject={nodeThreeObject}
            nodeThreeObjectExtend={false}
            onNodeClick={onNodeClick}
            onNodeHover={onNodeHover}
            backgroundColor="rgba(0,0,0,0)"
            linkWidth={getLinkWidth}
            linkColor={getLinkColor}
            linkOpacity={1}
            linkDirectionalParticles={getLinkParticles}
            linkDirectionalParticleWidth={getLinkParticleWidth}
            linkDirectionalParticleSpeed={0.005}
            linkDirectionalParticleColor={() => 'rgba(180,200,255,0.7)'}
        />
    );
};

export default MemoryGraphCanvas;
