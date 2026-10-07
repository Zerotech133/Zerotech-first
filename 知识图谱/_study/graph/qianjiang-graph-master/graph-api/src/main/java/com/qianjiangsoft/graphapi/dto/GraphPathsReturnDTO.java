package com.qianjiangsoft.graphapi.dto;

import lombok.Data;

import java.util.List;

/**
 * 查找回路和查找路径的接口返回值
 * @author ulmarr8861
 * @date 2022/4/26
 */
@Data
public class GraphPathsReturnDTO {
    private List<List<MergeEdgeDTO>> pathList;

    private List<GraphVertexReturnDTO> vertexList;
}
