package com.qianjiangsoft.graphapi.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.ArrayList;
import java.util.List;

/**
 * @author ulmarr8861
 * @date 2022/4/19
 */
@Data
@AllArgsConstructor
@NoArgsConstructor
public class GraphCommonReturnDTO {

    //所有的点
    private List<GraphVertexReturnDTO> vertexList = new ArrayList<>();

    //符合条件的边
    private List<MergeEdgeDTO> edgeList = new ArrayList<>();

    //符合条件的点
    private List<GraphVertexReturnDTO> searchVertexList = new ArrayList<>();
}
