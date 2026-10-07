package com.qianjiangsoft.graphapi.controller;

import com.qianjiangsoft.graphapi.vo.AddBaseRelationalGraphReturnVO;
import com.qianjiangsoft.graphcore.dto.BaseRelationalGraphDataDTO;
import com.qianjiangsoft.graphcore.dto.BaseRelationalGraphDetailDTO;
import com.qianjiangsoft.graphcore.dto.SelectBaseRelationalGraphListDTO;
import com.qianjiangsoft.graphcore.page.PageDataVO;
import com.qianjiangsoft.graphcore.param.AddBaseRelationalGraphParam;
import com.qianjiangsoft.graphcore.param.UpdateBaseRelationalGraphDataParam;
import com.qianjiangsoft.graphcore.param.UpdateBaseRelationalGraphParam;
import com.qianjiangsoft.graphapi.service.BaseRelationalGraphService;
import io.swagger.annotations.ApiOperation;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

/**
 * 基础-图谱接口
 * 
 * @author qianjiang
 * @date 2021-04-25
 */
@RestController
@RequestMapping("/base/relationalGraph")
@RequiredArgsConstructor
public class BaseRelationalGraphController {

	private final BaseRelationalGraphService baseRelationalGraphService;


	/**
	 * 查询基础-图谱分页查询
	 */
	@ApiOperation("图谱分页查询")
	@GetMapping("/selectGraphPageByLibraryId")
	public PageDataVO<SelectBaseRelationalGraphListDTO> selectGraphPageByLibraryId(String libraryId, String keyword) {
		return baseRelationalGraphService.selectGraphPageByLibraryId(libraryId, keyword);
	}


	/**
	 * 查询基础-图谱详情接口
	 */
	@ApiOperation("图谱详情接口")
	@GetMapping("/detail/{id}")
	public BaseRelationalGraphDetailDTO getGraphDetail(@PathVariable String id) {
		return baseRelationalGraphService.getGraphDetailById(id);
	}

	/**
	 * 查询基础-图谱详情接口
	 */
	@ApiOperation("图谱数据查询接口")
	@GetMapping("/data/{id}")
	public BaseRelationalGraphDataDTO getGraphData(@PathVariable String id) {
		return baseRelationalGraphService.getGraphDataById(id);
	}

	/**
	 * 查询基础-图谱新增接口
	 */
	@ApiOperation("图谱新增接口")
	@PostMapping("/add")
	public AddBaseRelationalGraphReturnVO getGraphDetail(@RequestBody AddBaseRelationalGraphParam param) {
		return AddBaseRelationalGraphReturnVO.builder().id(baseRelationalGraphService.addGraph(param)).build();
	}

	/**
	 * 查询基础-图谱更新接口
	 */
	@ApiOperation("图谱更新接口")
	@PostMapping("/update")
	public void updateGraph(@RequestBody UpdateBaseRelationalGraphParam param) {
		baseRelationalGraphService.updateGraph(param);
	}

	/**
	 * 查询基础-图谱更新接口
	 */
	@ApiOperation("图谱更新接口")
	@PostMapping("/data/update")
	public void updateGraphData(@RequestBody UpdateBaseRelationalGraphDataParam param) {
		baseRelationalGraphService.updateGraphData(param);
	}

	/**
	 * 查询基础-图谱更新接口
	 */
	@ApiOperation("图谱删除接口")
	@PostMapping("/delete/{id}")
	public void deleteGraphById(@PathVariable String id) {
		baseRelationalGraphService.deleteGraphById(id);
	}
}
