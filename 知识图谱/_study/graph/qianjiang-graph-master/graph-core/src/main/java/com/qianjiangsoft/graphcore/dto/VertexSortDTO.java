package com.qianjiangsoft.graphcore.dto;

import com.qianjiangsoft.graphcore.model.Vertex;
import lombok.Data;

@Data
public class VertexSortDTO {
    Double strength;
    Long count;
    Vertex vertex;
    Double propertyValue;
    Long outDegree;
    Long inDegree;
    Long allDegree;
}
