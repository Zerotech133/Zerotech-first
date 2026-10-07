package com.qianjiangsoft.graphapi.helper;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.map.MapUtil;
import cn.hutool.core.text.StrPool;
import cn.hutool.core.util.BooleanUtil;
import cn.hutool.core.util.StrUtil;
import com.qianjiangsoft.graphapi.dto.GraphCommonReturnDTO;
import com.qianjiangsoft.graphapi.dto.GraphVertexReturnDTO;
import com.qianjiangsoft.graphapi.dto.MergeEdgeDTO;
import com.qianjiangsoft.graphcore.constants.PropertyNameConstants;
import com.qianjiangsoft.graphcore.model.MergeEdge;
import com.qianjiangsoft.graphcore.model.Tag;
import com.qianjiangsoft.graphcore.model.Vertex;

import java.util.*;

/**
 * @author marshallwu
 * @date 2022/4/24 14:53
 */
public class GraphAnalyseHelper {

    /**
     * MergeEdgeList 转化为 MergeEdgeDTO
     * @param mergeEdgeList 合并边
     * @return 结果
     */
    public static List<MergeEdgeDTO> convertToMergeEdgeDTO(List<MergeEdge> mergeEdgeList){
        List<MergeEdgeDTO> resultList = new ArrayList<>();

        for(MergeEdge mergeEdge : mergeEdgeList) {
            MergeEdgeDTO mergeEdgeDTO = BeanUtil.copyProperties(mergeEdge, MergeEdgeDTO.class);
            mergeEdgeDTO.setSourceId(mergeEdge.getSource().getId());
            mergeEdgeDTO.setDestinationId(mergeEdge.getDestination().getId());
            mergeEdgeDTO.setEdgeTypeName(mergeEdge.getType().getName());
            resultList.add(mergeEdgeDTO);
        }
        return resultList;
    }

    public static GraphCommonReturnDTO convertMergeEdgeToGraphCommonReturnDTO(Map<String, Vertex> vertexMap, List<MergeEdge> mergeEdgeList) {
        //返回结果
        GraphCommonReturnDTO result = new GraphCommonReturnDTO();
        Map<String, GraphVertexReturnDTO> vertexReturnDTOMap = new HashMap<>();
        List<MergeEdgeDTO> edgeList = new ArrayList<>();


        if(vertexMap != null) {
            Iterator<Vertex> iterator = vertexMap.values().iterator();

            while (iterator.hasNext()) {
                Vertex vertex = iterator.next();
                GraphVertexReturnDTO vertexReturnDTO = convertVertexToGraphVertexDTO(vertex);
                result.getSearchVertexList().add(vertexReturnDTO);
                vertexReturnDTOMap.put(vertexReturnDTO.getId(), vertexReturnDTO);
            }
        }

        if(mergeEdgeList != null) {


            for(MergeEdge mergeEdge : mergeEdgeList) {
                MergeEdgeDTO mergeEdgeDTO = BeanUtil.copyProperties(mergeEdge, MergeEdgeDTO.class);
                mergeEdgeDTO.setSourceId(mergeEdge.getSource().getId());
                mergeEdgeDTO.setDestinationId(mergeEdge.getDestination().getId());
                mergeEdgeDTO.setEdgeTypeName(mergeEdge.getType().getName());
                edgeList.add(mergeEdgeDTO);

                //边两端点加入allVertexList
                GraphVertexReturnDTO vertexReturnDTO = convertVertexToGraphVertexDTO(mergeEdge.getSource());
                vertexReturnDTOMap.put(vertexReturnDTO.getId(), vertexReturnDTO);
                vertexReturnDTO = convertVertexToGraphVertexDTO(mergeEdge.getDestination());
                vertexReturnDTOMap.put(vertexReturnDTO.getId(), vertexReturnDTO);
            }

        }
        result.setEdgeList(edgeList);
        result.setVertexList(new ArrayList<>(vertexReturnDTOMap.values()));
        return result;
    }


    public static GraphVertexReturnDTO convertVertexToGraphVertexDTO(Vertex vertex) {
        GraphVertexReturnDTO graphVertexReturnDTO = BeanUtil.copyProperties(vertex, GraphVertexReturnDTO.class);
        Tag tag = vertex.getTag();
        if(tag != null) {
            graphVertexReturnDTO.setTagName(tag.getName());
        }
        return graphVertexReturnDTO;
    }

    public static boolean checkSuspectFlag(Map<String, Object> propertyMap) {
        String suspectFlag = MapUtil.getStr(propertyMap, PropertyNameConstants.suspectFlag);
        return StrUtil.equals(suspectFlag, "true");
    }

}
