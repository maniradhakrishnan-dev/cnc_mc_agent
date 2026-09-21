"""
Pydantic Models & Data Contract for Autonomous CNC Agent
Provides strict typed schemas for all pipeline stages:
- Tool Specifications & Catalog
- Extracted B-Rep Geometric Features & Canonical WCS
- Multi-Objective Strategies with Explicit Pre-Simulation Predictions
- CAMotics Kinematic Simulation & Collision Reports
- OpenCASCADE Euclidean Metrology & Surface Deviations
- Diagnostic Critique & Prediction Gap Analysis
- Formal Unmachinable Refusal Notices
"""

from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field

# ==============================================================================
# 1. Tool Library Schemas
# ==============================================================================

class ToolSpec(BaseModel):
    tool_number: int = Field(..., description="Unique tool slot/ID number")
    name: str = Field(..., description="Human-readable tool label")
    type: str = Field(..., description="Cutter type: endmill, ball_endmill, drill, chamfer")
    diameter_mm: float = Field(..., gt=0.0, description="Cutter cutting diameter in mm")
    flute_length_mm: float = Field(..., gt=0.0, description="Max depth of cut per pass/reach")
    overall_length_mm: float = Field(..., gt=0.0, description="Overall tool length")
    shank_diameter_mm: float = Field(..., gt=0.0, description="Holder shank diameter")
    flutes: int = Field(default=2, ge=1, description="Number of cutting flutes")
    chipload_min: float = Field(default=0.02, description="Min recommended chipload (mm/tooth)")
    chipload_max: float = Field(default=0.08, description="Max safe chipload (mm/tooth)")
    recommended_rpm: int = Field(default=8000, description="Baseline spindle RPM")
    feed_max: float = Field(default=2000.0, description="Max allowable linear feedrate")
    material: str = Field(default="Carbide", description="Substrate material")
    coating: str = Field(default="TiAlN", description="Tool coating")

class ToolLibrary(BaseModel):
    version: str = Field(default="1.0")
    description: str = Field(default="Standard CNC Milling Tool Library")
    tools: List[ToolSpec] = Field(..., description="Catalog of available tools")

# ==============================================================================
# 2. Geometric Features & Canonical WCS Schemas
# ==============================================================================

class BoundingBox(BaseModel):
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float

class CanonicalWCS(BaseModel):
    origin_type: str = Field(default="TOP_SURFACE_CENTER", description="Datum definition standard")
    delta_x: float = Field(default=0.0, description="Translation applied to align X=0")
    delta_y: float = Field(default=0.0, description="Translation applied to align Y=0")
    delta_z: float = Field(default=0.0, description="Translation applied to align Z_top=0")

class PocketFeature(BaseModel):
    id: str
    depth_mm: float = Field(..., gt=0.0)
    floor_z_mm: float
    internal_corner_radius_mm: Optional[float] = None
    is_through: bool = False
    is_stepped: bool = False
    island_count: int = 0
    area_mm2: Optional[float] = None

class HoleFeature(BaseModel):
    id: str
    diameter_mm: float = Field(..., gt=0.0)
    depth_mm: float = Field(..., gt=0.0)
    center_xy: List[float] = Field(..., min_items=2, max_items=2)
    is_through: bool = False

class MachinabilityConstraints(BaseModel):
    deepest_feature_depth_mm: float = 0.0
    min_internal_corner_radius_mm: Optional[float] = None
    thin_walls_detected: bool = False
    undercuts_detected: bool = False

class FeatureSet(BaseModel):
    source_cad_file: str
    material: str = Field(default="Aluminum 6061-T6")
    bounding_box: BoundingBox
    canonical_wcs: Optional[CanonicalWCS] = None
    machinability_constraints: MachinabilityConstraints
    pockets: List[PocketFeature] = Field(default_factory=list)
    holes: List[HoleFeature] = Field(default_factory=list)
    stock_dimensions_mm: Optional[List[float]] = None

# ==============================================================================
# 3. Machining Strategy & Pre-Simulation Prediction Schemas
# ==============================================================================

class OperationParameters(BaseModel):
    enabled: bool = True
    spindle_rpm: int = Field(..., gt=0)
    feedrate_mm_min: float = Field(..., gt=0)
    stepover_pct: float = Field(default=40.0, ge=1.0, le=100.0)
    stepdown_mm: float = Field(default=2.0, gt=0.0)
    ramp_plunge_angle_deg: float = Field(default=2.0, ge=0.5, le=45.0)
    finish_allowance_mm: float = Field(default=0.0, ge=0.0)
    spring_passes: int = Field(default=0, ge=0)

class DrillingParameters(BaseModel):
    spindle_rpm: int = Field(..., gt=0)
    feedrate_mm_min: float = Field(..., gt=0)
    peck_depth_mm: float = Field(default=3.0, gt=0.0)

class StrategyParameters(BaseModel):
    pocket_roughing: OperationParameters
    pocket_finishing: OperationParameters
    drilling: DrillingParameters

class StrategyPredictions(BaseModel):
    """
    Explicit quantitative predictions emitted by the LLM agent BEFORE simulation,
    satisfying REQ.md: 'learns from the gap between prediction and result'.
    """
    predicted_cycle_time_sec: float = Field(..., gt=0.0, description="Predicted machining cycle time in seconds")
    predicted_mean_deviation_um: float = Field(..., ge=0.0, description="Predicted mean surface deviation in microns")
    predicted_max_scallop_um: float = Field(..., ge=0.0, description="Predicted floor scallop height in microns")
    predicted_max_chipload_mm: float = Field(default=0.05, description="Predicted peak chip load in mm/tooth")

class MachiningStrategy(BaseModel):
    name: str
    description: str
    target_tradeoff: str
    tool_assignments: Dict[str, int] = Field(..., description="Mapping of operation to tool number")
    parameters: StrategyParameters
    predictions: StrategyPredictions

class StrategyPlan(BaseModel):
    part_file: str
    material: str = Field(default="Aluminum 6061-T6")
    strategies: Dict[str, MachiningStrategy] = Field(
        ..., description="Three Pareto frontier strategies: CYCLE_TIME, ACCURACY_TUNED, BALANCED"
    )

# ==============================================================================
# 4. Simulation & Physical Safety Schemas
# ==============================================================================

class RapidCollision(BaseModel):
    line_number: int
    x: float
    y: float
    z: float
    tool: int
    message: str = "Rapid move below clearance plane inside stock boundary"

class KinematicAudit(BaseModel):
    cycle_time_sec: float
    cycle_time_formatted: str
    rapid_distance_mm: float
    cut_distance_mm: float
    tool_changes: int
    tools_used: List[int]
    max_cut_depth_mm: float
    max_chipload_mm: float
    rapid_collisions: List[RapidCollision] = Field(default_factory=list)
    is_safe: bool = True

class StrategySimulation(BaseModel):
    gcode_file: str
    camotics_project: str
    cut_stl: str
    simulation_success: bool
    kinematics: KinematicAudit

class SimulationReport(BaseModel):
    strategies: Dict[str, StrategySimulation]

# ==============================================================================
# 5. Metrological Verification Schemas
# ==============================================================================

class GougeCheck(BaseModel):
    has_gouge: bool = False
    max_gouge_depth_um: float = 0.0
    status: str = "PASS"

class StrategyMetrology(BaseModel):
    name: str
    cycle_time_formatted: str
    cycle_time_sec: float
    cut_distance_mm: float
    target_removed_vol_mm3: float
    material_removed_mm3: float
    volumetric_fidelity_pct: float
    uncut_material_mm3: float
    floor_scallop_height_um: float
    mean_surface_deviation_um: float
    max_surface_deviation_um: float
    rms_surface_deviation_um: float
    tolerance_class: str
    verification_status: str
    is_verified: bool
    gouging_check: GougeCheck

class MetrologyReport(BaseModel):
    strategies: Dict[str, StrategyMetrology]

# ==============================================================================
# 6. Diagnostic Critique & Prediction Gap Schemas
# ==============================================================================

class CritiqueViolation(BaseModel):
    type: str = Field(..., description="RAPID_COLLISION, PART_GOUGE, CHIPLOAD_OVERLOAD, EXCESSIVE_SCALLOP, UNCUT_MATERIAL")
    severity: str = Field(..., description="CRITICAL, WARNING, QUALITY_DEFICIT, INFO")
    message: str
    tool: Optional[int] = None
    measured_value: Optional[float] = None
    target_value: Optional[float] = None

class PredictionGap(BaseModel):
    strategy: str
    predicted_cycle_time_sec: float
    measured_cycle_time_sec: float
    cycle_time_error_pct: float
    predicted_mean_dev_um: float
    measured_mean_dev_um: float
    deviation_error_um: float
    predicted_scallop_um: float
    measured_scallop_um: float
    scallop_error_um: float

class StrategyCritique(BaseModel):
    converged: bool
    status: str
    safety_pass: bool
    quality_pass: bool
    violations: List[CritiqueViolation] = Field(default_factory=list)
    adjustments: Dict[str, Any] = Field(default_factory=dict)
    prediction_gap: Optional[PredictionGap] = None

class CritiqueReport(BaseModel):
    converged: bool
    iteration_verdict: str
    total_violations: int
    strategies: Dict[str, StrategyCritique]
    actionable_feedback: List[str]

# ==============================================================================
# 7. The Refusal Notice Schema (REQ.md Line 41)
# ==============================================================================

class RefusalNotice(BaseModel):
    refusal_triggered: bool = True
    status: str = "REFUSED_UNMACHINABLE"
    offending_feature_id: str
    internal_corner_radius_mm: float
    smallest_available_tool_radius_mm: float
    required_tool_diameter_mm: float
    reason: str
    resolution_instructions: str
