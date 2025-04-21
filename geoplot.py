"""
geoplot.py
----------

This visualization renders a 3-D plot of the data given the state
trajectory of a simulation, and the path of the property to render.

It generates an HTML file that contains code to render the plot
using Cesium Ion, and the GeoJSON file of data provided to the plot.

An example of its usage is as follows:

```py
from agent_torch.visualize import GeoPlot

# create a simulation
# ...

# create a visualizer
engine = GeoPlot(config, {
  cesium_token: "...",
  step_time: 3600,
  coordinates = "agents/consumers/coordinates",
  feature = "agents/consumers/money_spent",
})

# visualize in the runner-loop
for i in range(0, num_episodes):
  runner.step(num_steps_per_episode)
  engine.render(runner.state_trajectory)
```
"""

import re # Regular expressions for string splitting
import json # For working with JSON (reading/writing GeoJSON)

import pandas as pd # Used to manage timestamps and time ranges
import numpy as np # Used for array manipulation and numerical processing

from string import Template # Used to substitute values in the HTML template
from agent_torch.core.helpers import get_by_path # Custom helper to extract nested data from a dict using a path

# This is a multi-line string that defines a complete HTML page with:
# 1. A 3D Cesium viewer.
# 2. Functions to interpolate color or size over time.
# 3. A script that loads your GeoJSON and dynamically creates animated entities.

geoplot_template = """
<!doctype html>
<html lang="en">
	<head>
		<meta charset="UTF-8" />
		<meta
			name="viewport"
			content="width=device-width, initial-scale=1.0"
		/>
		<title>Cesium Time-Series Heatmap Visualization</title>
		<script src="https://cesium.com/downloads/cesiumjs/releases/1.95/Build/Cesium/Cesium.js"></script>
		<link
			href="https://cesium.com/downloads/cesiumjs/releases/1.95/Build/Cesium/Widgets/widgets.css"
			rel="stylesheet"
		/>
		<style>
			#cesiumContainer {
				width: 100%;
				height: 100%;
			}
		</style>
	</head>
	<body>
		<div id="cesiumContainer"></div>
		<script>
			// Your Cesium ion access token here
			Cesium.Ion.defaultAccessToken = '$accessToken'

			// Create the viewer
			const viewer = new Cesium.Viewer('cesiumContainer')

			function interpolateColor(color1, color2, factor) {
				const result = new Cesium.Color()
				result.red = color1.red + factor * (color2.red - color1.red)
				result.green =
					color1.green + factor * (color2.green - color1.green)
				result.blue = color1.blue + factor * (color2.blue - color1.blue)
				result.alpha = '$visualType' == 'size' ? 0.2 :
					color1.alpha + factor * (color2.alpha - color1.alpha)
				return result
			}

			function getColor(value, min, max) {
				const factor = (value - min) / (max - min)
				return interpolateColor(
					Cesium.Color.BLUE,
					Cesium.Color.RED,
					factor
				)
			}

			function getPixelSize(value, min, max) {
				const factor = (value - min) / (max - min)
				return 100 * (1 + factor)
			}

			function processTimeSeriesData(geoJsonData) {
				const timeSeriesMap = new Map()
				let minValue = Infinity
				let maxValue = -Infinity

				geoJsonData.features.forEach((feature) => {
					const id = feature.properties.id
					const time = Cesium.JulianDate.fromIso8601(
						feature.properties.time
					)
					const value = feature.properties.value
					const coordinates = feature.geometry.coordinates

					if (!timeSeriesMap.has(id)) {
						timeSeriesMap.set(id, [])
					}
					timeSeriesMap.get(id).push({ time, value, coordinates })

					minValue = Math.min(minValue, value)
					maxValue = Math.max(maxValue, value)
				})

				return { timeSeriesMap, minValue, maxValue }
			}

			function createTimeSeriesEntities(
				timeSeriesData,
				startTime,
				stopTime
			) {
				const dataSource = new Cesium.CustomDataSource(
					'AgentTorch Simulation'
				)

				for (const [id, timeSeries] of timeSeriesData.timeSeriesMap) {
					const entity = new Cesium.Entity({
						id: id,
						availability: new Cesium.TimeIntervalCollection([
							new Cesium.TimeInterval({
								start: startTime,
								stop: stopTime,
							}),
						]),
						position: new Cesium.SampledPositionProperty(),
						point: {
							pixelSize: '$visualType' == 'size' ? new Cesium.SampledProperty(Number) : 10,
							color: new Cesium.SampledProperty(Cesium.Color),
						},
						properties: {
							value: new Cesium.SampledProperty(Number),
						},
					})

					timeSeries.forEach(({ time, value, coordinates }) => {
						const position = Cesium.Cartesian3.fromDegrees(
							coordinates[0],
							coordinates[1]
						)
						entity.position.addSample(time, position)
						entity.properties.value.addSample(time, value)
						entity.point.color.addSample(
							time,
							getColor(
								value,
								timeSeriesData.minValue,
								timeSeriesData.maxValue
							)
						)

						if ('$visualType' == 'size') {
						  entity.point.pixelSize.addSample(
  							time,
  							getPixelSize(
  								value,
  								timeSeriesData.minValue,
  								timeSeriesData.maxValue
  							)
  						)
						}
					})

					dataSource.entities.add(entity)
				}

				return dataSource
			}

			// Example time-series GeoJSON data
			const geoJsons = $data

			const start = Cesium.JulianDate.fromIso8601('$startTime')
			const stop = Cesium.JulianDate.fromIso8601('$stopTime')

			viewer.clock.startTime = start.clone()
			viewer.clock.stopTime = stop.clone()
			viewer.clock.currentTime = start.clone()
			viewer.clock.clockRange = Cesium.ClockRange.LOOP_STOP
			viewer.clock.multiplier = 3600 // 1 hour per second

			viewer.timeline.zoomTo(start, stop)

			for (const geoJsonData of geoJsons) {
				const timeSeriesData = processTimeSeriesData(geoJsonData)
				const dataSource = createTimeSeriesEntities(
					timeSeriesData,
					start,
					stop
				)
				viewer.dataSources.add(dataSource)
				viewer.zoomTo(dataSource)
			}
		</script>
	</body>
</html>
"""

# Helper Function
# Purpose : Extract a deeply nested variable from the state using a path like agents/consumers/coordinates.
def read_var(state, var):
# re.split("/", var) turns the path string into a list
    return get_by_path(state, re.split("/", var)) # get_by_path walks through the state dict to reach the target value.

# Main Class, used to visualize simulation data on a 3D Cesium globe over time.
class GeoPlot:
    def __init__(self, config, options):
        self.config = config # The simulation configuration
	# Dictionary that includes:
        (
            self.cesium_token, 
            self.step_time,
            self.entity_position,
            self.entity_property,
            self.visualization_type,
        ) = (
            options["cesium_token"], # API token for Cesium Ion
            options["step_time"], # Time interval between each simulation step (e.g., 3600 seconds)
            options["coordinates"], # Path in the state to find positions
            options["feature"], # Path to find the value to visualize (e.g., money spent)
            options["visualization_type"], # Type of visualization (color vs size)
        )

# This method handles the entire rendering process.
    def render(self, state_trajectory):
        coords, values = [], []
	# Uses simulation name to determine output filenames for simulation data and final visualization respectively.
        name = self.config["simulation_metadata"]["name"]
        geodata_path, geoplot_path = f"{name}.geojson", f"{name}.html"

	# Loops over every episode in order to get final state from each episode to extract coordinates and values for that step and finally storing it as a list of lists.
        for i in range(0, len(state_trajectory) - 1):
            final_state = state_trajectory[i][-1]

            coords = np.array(read_var(final_state, self.entity_position)).tolist()
            values.append(
                np.array(read_var(final_state, self.entity_property)).flatten().tolist()
            )

	#Create an ordered list of timestamps for each simulation step. Each step is offset by step_time.
        start_time = pd.Timestamp.utcnow()
        timestamps = [
            start_time + pd.Timedelta(seconds=i * self.step_time)
            for i in range(
                self.config["simulation_metadata"]["num_episodes"]
                * self.config["simulation_metadata"]["num_steps_per_episode"]
            )
        ]

	# For each coordinate, construct a GeoJSON time series with timestamps and values(Coordinates must be in [longitude, latitude] format).
        geojsons = []
        for i, coord in enumerate(coords):
            features = []
            for time, value_list in zip(timestamps, values):
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [coord[1], coord[0]],
                        },
                        "properties": {
                            "value": value_list[i],
                            "time": time.isoformat(),
                        },
                    }
                )
            geojsons.append({"type": "FeatureCollection", "features": features})

	# Writes the structured GeoJSON data to a file.
        with open(geodata_path, "w", encoding="utf-8") as f:
            json.dump(geojsons, f, ensure_ascii=False, indent=2)

	# Render HTML File 
	# Uses Python Template to insert values into HTML string and Saves the final HTML page to a file.
        tmpl = Template(geoplot_template)
        with open(geoplot_path, "w", encoding="utf-8") as f:
            f.write(
                tmpl.substitute(
                    {
                        "accessToken": self.cesium_token,
                        "startTime": timestamps[0].isoformat(),
                        "stopTime": timestamps[-1].isoformat(),
                        "data": json.dumps(geojsons),
                        "visualType": self.visualization_type,
                    }
                )
            )


# Summary of What It Does :-
# 1. Extracts positions and properties over time from a simulation.

# 2. Converts the data to time-encoded GeoJSON format.

# 3. Writes the GeoJSON and an HTML page that visualizes it using CesiumJS.

# 4. You can open the generated HTML file in a browser to view a 3D animated heatmap or size-based plot.
