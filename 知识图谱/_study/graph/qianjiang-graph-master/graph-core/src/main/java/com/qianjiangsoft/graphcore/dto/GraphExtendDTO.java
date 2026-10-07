package com.qianjiangsoft.graphcore.dto;

import com.qianjiangsoft.graphcore.model.Edge;
import com.qianjiangsoft.graphcore.model.MergeEdge;
import com.qianjiangsoft.graphcore.model.Vertex;
import lombok.Data;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * @author marshallwu
 * @date 2022/2/24 15:48
 */
@Data
public class GraphExtendDTO {
    Map<String, Vertex> vertexMap = new HashMap<>();
    List<Edge> edgeList = new ArrayList<>();
    List<MergeEdge> mergeEdgeList = new ArrayList<>();
}
