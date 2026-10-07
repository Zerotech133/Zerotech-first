package com.qianjiangsoft.graphcore.dto;

import com.qianjiangsoft.graphcore.model.Vertex;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;
import java.util.Map;

/**
 * @author marshallwu
 * @date 2022/2/23 16:37
 */
@AllArgsConstructor
@Data
@NoArgsConstructor
public class FlowDirectionDTO {

    Map<String, Vertex> vertexMap;
    List<FlowDirectionEdgeDTO> edgeList;
}
