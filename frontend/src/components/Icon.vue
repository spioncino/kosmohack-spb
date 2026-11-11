<script setup lang="ts">
import {
  Activity, Archive, ArrowDownToLine, ArrowRight, ArrowUpRight, Ban, BookOpen, Braces, CalendarClock, CalendarDays,
  ChartNoAxesColumnIncreasing, Check, CheckCircle2, ChevronDown, ChevronRight, ChevronUp, Circle, CircleDot, CircleHelp,
  CircleOff, ClipboardCheck, Clock3, Clock4, CloudSun, Code, Columns3, Compass, Crosshair, Database, Download, Eclipse,
  Expand, ExternalLink, FileJson, FileText, FolderArchive, Gauge, Globe, Globe2, History, Hourglass, Info, Layers, LoaderCircle,
  Magnet, MapPin, Map, Menu, MessageCircleQuestion, Moon, MousePointer2, MoveVertical, Orbit, Package, Pause, Play, Power, Radar, Radiation,
  RadioTower, RefreshCw, Rocket, RotateCw, Satellite, Save, Scale, Search, Send, Settings, ShieldAlert, ShieldCheck, Shrink,
  SlidersHorizontal, Snowflake, Sparkles, Square, SquareCheck, Sun, Table, Timer, Trash2, TriangleAlert, Unplug, User,
  Waves, WifiOff, X, Zap,
} from 'lucide-vue-next';
import { computed } from 'vue';

const icons: Record<string, unknown> = {
  Activity, Archive, ArrowDownToLine, ArrowRight, ArrowUpRight, Ban, BookOpen, Braces, CalendarClock, CalendarDays,
  ChartNoAxesColumnIncreasing, Check, CheckCircle2, ChevronDown, ChevronRight, ChevronUp, Circle, CircleDot, CircleHelp,
  CircleOff, ClipboardCheck, Clock3, Clock4, CloudSun, Code, Columns3, Compass, Crosshair, Database, Download, Eclipse,
  Expand, ExternalLink, FileJson, FileText, FolderArchive, Gauge, Globe, Globe2, History, Hourglass, Info, Layers, LoaderCircle,
  Magnet, MapPin, Map, Menu, MessageCircleQuestion, Moon, MousePointer2, MoveVertical, Orbit, Package, Pause, Play, Power, Radar, Radiation,
  RadioTower, RefreshCw, Rocket, RotateCw, Satellite, Save, Scale, Search, Send, Settings, ShieldAlert, ShieldCheck, Shrink,
  SlidersHorizontal, Snowflake, Sparkles, Square, SquareCheck, Sun, Table, Timer, Trash2, TriangleAlert, Unplug, User,
  Waves, WifiOff, X, Zap,
};
const props = withDefaults(defineProps<{ name: string; size?: number }>(), { size: 20 });
const selected = computed(() => (icons[props.name] || Circle) as never);
</script>
<template><component :is="selected" :size="size" :stroke-width="1.65" aria-hidden="true" /></template>
