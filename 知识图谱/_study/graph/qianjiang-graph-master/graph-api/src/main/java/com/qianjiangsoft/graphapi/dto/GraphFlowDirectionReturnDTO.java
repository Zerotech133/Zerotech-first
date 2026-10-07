package com.qianjiangsoft.graphapi.dto;

import com.qianjiangsoft.graphcore.dto.FlowDirectionEdgeDTO;
import com.qianjiangsoft.graphcore.model.Vertex;
import lombok.Data;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * @author ulmarr8861
 * @date 2022/5/16
 */
@Data
public class GraphFlowDirectionReturnDTO {
    private List<List<FlowDirectionEdgeDTO>> edgeList;
    private List<Map<String, Vertex>> vertexList;
}
