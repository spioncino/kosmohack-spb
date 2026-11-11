<script setup lang="ts">
import {ref,onMounted,onUnmounted} from 'vue';
import * as T from 'three';
import {renderStationFallback} from '../stationFallback';
const host=ref<HTMLElement>();let renderer:T.WebGLRenderer|undefined;let frame=0;
onMounted(()=>{if(!host.value)return;try{renderer=new T.WebGLRenderer({alpha:true,antialias:true});}catch{onCleanup=renderStationFallback(host.value);return;}const el=host.value;renderer.setPixelRatio(Math.min(devicePixelRatio,2));el.appendChild(renderer.domElement);const scene=new T.Scene();const camera=new T.PerspectiveCamera(36,1,.1,100);camera.position.set(5,3,6);camera.lookAt(0,0,0);scene.add(new T.AmbientLight(0xa2bed0,2));const light=new T.DirectionalLight(0xffffff,3);light.position.set(3,5,4);scene.add(light);const group=new T.Group();scene.add(group);
const metal=new T.MeshStandardMaterial({color:0xbcc8cc,metalness:.65,roughness:.42});const solar=new T.MeshStandardMaterial({color:0x34435e,metalness:.5,roughness:.45,side:T.DoubleSide});const gold=new T.LineBasicMaterial({color:0xb99b64});
function box(x:number,y:number,z:number,w:number,h:number,d:number,mat:T.Material){const b=new T.Mesh(new T.BoxGeometry(w,h,d),mat);b.position.set(x,y,z);group.add(b);return b;}
box(0,0,0,4.5,.10,.12,metal);for(let x=-1.8;x<=1.8;x+=1.2){for(const y of [-.9,.9]){const b=box(x,y,0,.58,1.48,.03,solar);const edges=new T.LineSegments(new T.EdgesGeometry(b.geometry),gold);edges.position.copy(b.position);group.add(edges);for(let i=0;i<7;i++)box(x,y-.7+i*.22,.022,.57,.01,.01,metal);}}
for(let i=0;i<4;i++){const c=new T.Mesh(new T.CylinderGeometry(.18,.18,.7,16),metal);c.rotation.x=Math.PI/2;c.position.set(0,.04,-1.1+i*.67);group.add(c);}box(.4,.08,.1,.8,.24,.3,metal);box(-.4,.08,.7,.8,.24,.3,metal);group.rotation.z=-.2;
const resize=new ResizeObserver(()=>{const {width,height}=el.getBoundingClientRect();renderer!.setSize(width,height);camera.aspect=width/height;camera.updateProjectionMatrix();});resize.observe(el);const render=()=>{frame=requestAnimationFrame(render);renderer!.render(scene,camera);};render();onCleanup=()=>{resize.disconnect();scene.traverse(o=>{if(o instanceof T.Mesh||o instanceof T.LineSegments)o.geometry.dispose();});metal.dispose();solar.dispose();gold.dispose();};});let onCleanup=()=>{};
onUnmounted(()=>{cancelAnimationFrame(frame);onCleanup();renderer?.dispose();});
</script>
<template><div ref="host" class="station-model" role="img" aria-label="Схематическая трёхмерная модель МКС"/></template>
